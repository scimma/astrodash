"""Central declarative registry of the built-in classifier models.

This package is the single source of truth for the built-in classifiers (DASH
and Transformer). Each model is described once as a :class:`ModelDefinition` in
its own module under :mod:`.definitions`, carrying its UI card fields, lifecycle
status, default flag, capability and requirement flags, and a reference to the
classifier that runs it. Forms, the model-selection template, the classifier
factory, and the behavioral gates all read from here instead of scattering
``"dash"`` / ``"transformer"`` string literals across the codebase.

Adding a built-in model is one new ``definitions/<id>.py`` module plus a single
entry in ``MODELS`` here (and the model's ``config/settings.py`` paths and its
classifier). Retiring a model is a single ``status`` flip on its definition: a
retired model leaves the active surfaces (cards, form choices) but still
resolves its label and fields for any stored result that references it.

The registry is deliberately code-native. A data-driven or self-registering
registry for independently contributed models is a later runway, not built
here.
"""

from typing import AbstractSet, Mapping, Optional, Tuple

from astrodash.infrastructure.ml.model_registry._model_definition import (
    REDSHIFT_INPUT_NONE,
    REDSHIFT_INPUT_OPTIONAL,
    REDSHIFT_INPUT_REQUIRED,
    STATUS_ACTIVE,
    STATUS_RETIRED,
    SURFACE_CLASSIFICATION,
    SURFACE_DASH_TWINS,
    ModelDefinition,
)
from astrodash.infrastructure.ml.model_registry.definitions.dash import DASH
from astrodash.infrastructure.ml.model_registry.definitions.latent_noz import (
    LATENT_NOZ,
)
from astrodash.infrastructure.ml.model_registry.definitions.latent_z import LATENT_Z
from astrodash.infrastructure.ml.model_registry.definitions.oned_cnn_noz import (
    ONED_CNN_NOZ,
)
from astrodash.infrastructure.ml.model_registry.definitions.oned_cnn_z import (
    ONED_CNN_Z,
)
from astrodash.infrastructure.ml.model_registry.definitions.transformer import (
    TRANSFORMER,
)

# Public surface re-exported from this package. Declaring it keeps the status
# constants (imported here purely to preserve `from ...model_registry import
# STATUS_*` for callers) from reading as unused, so a future lint/autofix pass
# cannot strip the re-export and break those import sites.
__all__ = [
    "ModelDefinition",
    "STATUS_ACTIVE",
    "STATUS_RETIRED",
    "REDSHIFT_INPUT_REQUIRED",
    "REDSHIFT_INPUT_OPTIONAL",
    "REDSHIFT_INPUT_NONE",
    "SURFACE_CLASSIFICATION",
    "SURFACE_DASH_TWINS",
    "MODELS",
    "validate_registry",
    "validate_gate_configuration",
    "validate_surfaces",
    "get_definition",
    "active_definitions",
    "listed_definitions",
    "default_definition",
]

# Ordered registry of built-in classifiers. The order here is the order the
# cards render on the model-selection page (Transformer first, then DASH,
# then the website_final 1D CNN and latent variants).
MODELS: Tuple[ModelDefinition, ...] = (
    TRANSFORMER,
    DASH,
    ONED_CNN_Z,
    ONED_CNN_NOZ,
    LATENT_Z,
    LATENT_NOZ,
)


def validate_registry(models: Tuple[ModelDefinition, ...]) -> None:
    """Validate registry invariants.

    Args:
        models: The collection of model definitions to validate.

    Raises:
        ValueError: If model ids are not unique, if the number of active
            definitions marked as default is not exactly one, if a definition
            requiring a credential is also listed, or if the active default is
            unlisted or requires a credential.
    """
    ids = [m.id for m in models]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate model ids in registry: {ids}")

    active_defaults = [m for m in models if m.is_active and m.is_default]
    if len(active_defaults) != 1:
        raise ValueError(
            "Exactly one active model must be the default; "
            f"found {len(active_defaults)}: {[m.id for m in active_defaults]}"
        )

    # A gated model is never listed: listing it would advertise a model that
    # only a credentialed visitor can run, and would leak its existence.
    gated_and_listed = [m.id for m in models if m.requires_credential and m.listed]
    if gated_and_listed:
        raise ValueError(
            "A model requiring a credential must not be listed; "
            f"offending models: {gated_and_listed}"
        )

    # The default is what every visitor lands on, so it must be reachable by
    # anyone and visible on the selection surfaces.
    default = active_defaults[0]
    if not default.listed or default.requires_credential:
        raise ValueError(
            "The active default model must be listed and must not require a "
            f"credential; '{default.id}' is listed={default.listed}, "
            f"requires_credential={default.requires_credential}"
        )


def validate_gate_configuration(
    models: Tuple[ModelDefinition, ...],
    configuration: Mapping[str, Optional[str]],
) -> None:
    """Validate that a roster containing a gated model is configured to serve it.

    A gated model must never be served without its credential prompt, so a
    deployment missing any part of the gate's configuration fails closed: the
    caller (the app-ready hook) lets this propagate and the process refuses to
    start.

    Configuration is passed in rather than read here, because nothing under the
    ML infrastructure package imports Django or reads the environment. The
    caller normalizes each value, mapping "unset", "blank", and "left at a
    committed default" alike to ``None``.

    Args:
        models: The collection of model definitions to check.
        configuration: Mapping of configuration name (as an operator sets it)
            to its normalized value, with ``None`` meaning unconfigured.

    Raises:
        ValueError: If any model requires a credential while one or more
            configuration values are unconfigured. The message names both the
            gated models and the missing configuration.
    """
    gated = [m.id for m in models if m.requires_credential]
    if not gated:
        return

    missing = sorted(name for name, value in configuration.items() if value is None)
    if missing:
        raise ValueError(
            "Gated models require gate configuration that is unset, empty, or "
            f"left at a committed default: {missing}. "
            f"Gated models in the registry: {gated}."
        )


def validate_surfaces(
    models: Tuple[ModelDefinition, ...],
    known_surface_ids: AbstractSet[str],
) -> None:
    """Validate the result surfaces every definition declares.

    The known ids are passed in rather than imported, because a surface id only
    means something once the web layer can render it, and nothing under the ML
    infrastructure package imports Django or knows about URL names. The caller
    (the app-ready hook) supplies the web layer's surface map keys, so an id
    with no presentation refuses startup instead of rendering as a broken tab.

    Args:
        models: The collection of model definitions to check.
        known_surface_ids: Every surface id the web layer can resolve.

    Raises:
        ValueError: If a definition declares no surfaces, omits the
            classification surface, or declares an id the web layer does not
            know. The message names the offending model and ids.
    """
    for model in models:
        if not model.surfaces:
            raise ValueError(
                f"Model '{model.id}' declares no result surfaces; every model "
                f"must declare at least '{SURFACE_CLASSIFICATION}'."
            )

        unknown = [s for s in model.surfaces if s not in known_surface_ids]
        if unknown:
            raise ValueError(
                f"Model '{model.id}' declares unknown result surfaces: "
                f"{unknown}. Known surfaces: {sorted(known_surface_ids)}."
            )

        if SURFACE_CLASSIFICATION not in model.surfaces:
            raise ValueError(
                f"Model '{model.id}' must declare the "
                f"'{SURFACE_CLASSIFICATION}' surface; declared: "
                f"{list(model.surfaces)}."
            )


def get_definition(model_id: str) -> Optional[ModelDefinition]:
    """Resolve a model id to its definition, active or retired.

    Args:
        model_id: The model identifier (``model_type``) to look up.

    Returns:
        The matching :class:`ModelDefinition`, or ``None`` if no built-in model
        has that id (e.g. a user-uploaded model type), so callers can fall back
        to their non-built-in behavior.
    """
    for definition in MODELS:
        if definition.id == model_id:
            return definition
    return None


def active_definitions() -> Tuple[ModelDefinition, ...]:
    """Return the active model definitions in registry order.

    Returns:
        The definitions whose status is :data:`STATUS_ACTIVE`, in the order they
        appear in ``MODELS``.
    """
    return tuple(m for m in MODELS if m.is_active)


def listed_definitions() -> Tuple[ModelDefinition, ...]:
    """Return the definitions offered on the selection surfaces, in order.

    Listing and lifecycle status are separate questions: a model can be active
    (it runs, and it resolves by id) while being unlisted (no card, no choice
    control). A retired model is never listed.

    Returns:
        The definitions that are both active and listed, in the order they
        appear in ``MODELS``.
    """
    return tuple(m for m in MODELS if m.is_active and m.listed)


def default_definition() -> ModelDefinition:
    """Return the default active model definition.

    Returns:
        The single active definition marked as the default.

    Raises:
        ValueError: If the exactly-one-active-default invariant does not hold.
    """
    defaults = [m for m in MODELS if m.is_active and m.is_default]
    if len(defaults) != 1:
        raise ValueError(
            "Exactly one active model must be the default; " f"found {len(defaults)}"
        )
    return defaults[0]


# Enforce the registry invariants at import time.
validate_registry(MODELS)
