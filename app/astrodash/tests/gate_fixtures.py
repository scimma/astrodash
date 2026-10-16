"""Shared fixtures for the gated-model access gate.

The shipped roster contains no gated model -- that is the point of the
capability, not an oversight -- so every test that needs one builds it here:
``dataclasses.replace`` over a real definition plus a patched roster (KTD9).
A fixture built this way never passes through the import-time validators, so a
test that wants to exercise an invariant calls the validator directly rather
than reloading modules.

The gate's deployment values are patched in the same way, and the classify view
is driven with its services mocked, so nothing here needs model weights, network
access, or a configured deployment.
"""

import os
from contextlib import contextmanager
from dataclasses import replace
from typing import Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

from django.http import HttpResponse
from django.urls import reverse

from astrodash.core import gate_config, model_access
from astrodash.infrastructure.ml import model_registry
from astrodash.infrastructure.ml.model_registry import ModelDefinition

# The gate configuration every test runs against. Neither the credential nor the
# signing key may be blank or carry the committed ``django-insecure-`` prefix,
# or the gate reads them as unconfigured and fails closed.
GATE_CREDENTIAL = "review-window-access-code"
GATE_SIGNING_KEY = "test-entry-link-signing-key"
GATE_LINK_BASE_URL = "https://astrodash-dev.example.org"


# The listed, ungated model a patched roster promotes to default when the model
# it gates (or unlists, or retires) was the default. Every patched roster must
# still hold exactly one active, listed, ungated default -- patching ``MODELS``
# skips the import-time validators, so an invalid roster would otherwise go
# unnoticed. ``1dCNN_z`` is the earliest such model in registry order after DASH.
STAND_IN_DEFAULT = "1dCNN_z"


def promote_stand_in_default(
    models: Tuple[ModelDefinition, ...], displaced: str
) -> Tuple[ModelDefinition, ...]:
    """Move the default flag off one model and onto the stand-in default.

    Args:
        models: A ``MODELS``-shaped tuple.
        displaced: The id that can no longer be the default in this roster.
            If it is not the default, the roster is returned unchanged.

    Returns:
        A ``MODELS``-shaped tuple whose default, if it was ``displaced``, is
        now :data:`STAND_IN_DEFAULT`.
    """
    if not any(m.id == displaced and m.is_default for m in models):
        return models

    def _rebind(model: ModelDefinition) -> ModelDefinition:
        if model.id == displaced:
            return replace(model, is_default=False)
        if model.id == STAND_IN_DEFAULT:
            return replace(model, is_default=True)
        return model

    return tuple(_rebind(m) for m in models)


def gated_roster(model_id: str = "dash") -> Tuple[ModelDefinition, ...]:
    """Return a roster in which one built-in model is gated.

    If the gated model was the default, :data:`STAND_IN_DEFAULT` becomes the
    default instead, because the registry forbids a gated default. The result
    is checked with ``validate_registry`` so no caller tests an impossible
    roster.

    Args:
        model_id: The built-in id to gate. It is made unlisted at the same
            time, because the registry forbids a gated model from being listed.

    Returns:
        A ``MODELS``-shaped tuple suitable for ``patch.object``.

    Raises:
        ValueError: If the resulting roster violates a registry invariant.
    """
    roster = tuple(
        replace(m, listed=False, requires_credential=True) if m.id == model_id else m
        for m in promote_stand_in_default(model_registry.MODELS, model_id)
    )
    model_registry.validate_registry(roster)
    return roster


@contextmanager
def gate_configured(
    credential: Optional[str] = GATE_CREDENTIAL,
    ttl_seconds: Optional[str] = "3600",
    signing_key: Optional[str] = GATE_SIGNING_KEY,
):
    """Configure the gate's deployment values for the duration of the block.

    The normalized configuration is patched in rather than driven through the
    environment, because the entry-link signing key *is* ``SECRET_KEY``: moving
    the real setting would re-sign the test client's session cookie mid-test and
    every session assertion would then read an empty session instead of the one
    the gate wrote. ``gate_config``'s own normalization is covered by the
    registry tests. The link host is a separate value that no session depends
    on, so it stays an environment read.

    Args:
        credential: The shared credential an operator would configure.
        ttl_seconds: The entry-link expiry window, as an operator sets it.
        signing_key: The key entry links are signed with.

    Yields:
        None.
    """
    configuration = {
        gate_config.CREDENTIAL_ENV_VAR: credential,
        gate_config.LINK_TTL_ENV_VAR: ttl_seconds,
        gate_config.SIGNING_KEY_NAME: signing_key,
    }
    with patch.object(
        model_access, "gate_configuration", return_value=configuration
    ), patch.dict(os.environ, {gate_config.LINK_BASE_URL_ENV_VAR: GATE_LINK_BASE_URL}):
        yield


@contextmanager
def gated_gate(model_id: str = "dash", **config):
    """Patch in a gated roster *and* the gate configuration together.

    Args:
        model_id: The built-in id to gate.
        **config: Passed through to :func:`gate_configured`.

    Yields:
        None.
    """
    with patch.object(model_registry, "MODELS", gated_roster(model_id)):
        with gate_configured(**config):
            yield


def gate_url(token: str) -> str:
    """Return the entry-link path carrying a token.

    Args:
        token: The signed entry-link token.

    Returns:
        The path an entry link resolves to.
    """
    return reverse("astrodash:model_gate", args=[token])


def classify_post(model: str) -> dict:
    """Return a valid classify-form payload naming a model.

    Args:
        model: The value to put in the form's model field. Inside a scoped flow
            this is exactly the field an attacker would alter, so tests vary it
            independently of the scope.

    Returns:
        The POST data.
    """
    return {
        "supernova_name": "SN2011fe",
        "model": model,
        "smoothing": 0,
        "min_wave": 3500,
        "max_wave": 10000,
    }


def redeem_scope(client, test_case, now=1000.0, ttl_seconds="3600", model_id="dash"):
    """Establish a scope on a test client by redeeming a fresh entry link.

    Args:
        client: The Django test client to scope.
        test_case: The calling ``TestCase``, used to assert the redemption
            actually succeeded rather than silently leaving the client unscoped.
        now: The POSIX timestamp to mint and redeem at.
        ttl_seconds: The configured expiry window.
        model_id: The built-in id to gate and scope to.

    Returns:
        float: The absolute deadline the scope was granted with.
    """
    with gated_gate(model_id, ttl_seconds=ttl_seconds), patch.object(
        model_access, "_now", return_value=now
    ):
        token = model_access.mint_entry_link(model_id)
        response = client.post(gate_url(token), data={"credential": GATE_CREDENTIAL})
    test_case.assertEqual(response.status_code, 302)
    return client.session[model_access.SCOPE_DEADLINE_KEY]


@contextmanager
def mocked_classification(model_type: str):
    """Run the classify view without model weights or network access.

    Args:
        model_type: The model the mocked classification reports having run.

    Yields:
        The mocked classification service, so a test can read back which model
        the view actually asked it to run.
    """
    processed = MagicMock()
    processed.x = [3000.0, 5000.0, 9000.0]
    processed.y = [1.0, 2.0, 1.0]

    classification = MagicMock()
    classification.model_type = model_type
    classification.results = {"best_matches": [], "embedding": [0.0] * 1024}

    classification_svc = MagicMock(
        classify_spectrum=AsyncMock(return_value=classification)
    )
    with patch(
        "astrodash.ui_views.get_spectrum_service",
        return_value=MagicMock(get_spectrum_data=AsyncMock(return_value=MagicMock())),
    ), patch(
        "astrodash.ui_views.get_spectrum_processing_service",
        return_value=MagicMock(
            process_spectrum_with_params=AsyncMock(return_value=processed)
        ),
    ), patch(
        "astrodash.ui_views.get_classification_service",
        return_value=classification_svc,
    ), patch(
        "astrodash.ui_views.render", return_value=HttpResponse(b"")
    ):
        yield classification_svc
