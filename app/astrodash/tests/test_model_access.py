"""The gated-model access gate: entry links, the credential prompt, the scope.

A gated model (one whose definition declares ``requires_credential``) is
reachable only by redeeming a model-scoped entry link and presenting the shared
credential, which establishes a session scoped to that one model.

Every test here builds its gated model with ``dataclasses.replace`` over a real
definition plus a patched roster (KTD9): DASH becomes gated -- and therefore
unlisted, which the registry invariants require -- and the default flag moves to
``1dCNN_z`` so the roster keeps exactly one listed, ungated active default.
Such a fixture never passes through the import-time validators, so nothing here
reloads modules.

These need a database: no session engine is configured, so sessions are
database-backed and anything driving the prompt or the scope through the test
client hits the sessions table.
"""

import os
import re
from dataclasses import replace
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.urls import reverse

from astrodash.core import gate_config, model_access
from astrodash.infrastructure.ml import model_registry
from astrodash.tests.gate_fixtures import (
    GATE_CREDENTIAL,
    GATE_LINK_BASE_URL,
    classify_post,
    gate_configured,
    gate_url,
    gated_gate,
    gated_roster,
    mocked_classification,
    promote_stand_in_default,
    redeem_scope,
)


class EntryLinkTokenTests(TestCase):
    """R6/R8/R28: what a minted token carries and when it stops resolving."""

    def test_minted_link_resolves_to_its_model(self):
        with gated_gate():
            token = model_access.mint_entry_link("dash")
            link = model_access.redeem_entry_link(token)
        self.assertEqual(link.model_id, "dash")

    def test_deadline_is_stamped_at_mint_not_evaluated_later(self):
        """KTD2: shortening the window cannot move an outstanding link's deadline."""
        with gated_gate(ttl_seconds="3600"), patch.object(
            model_access, "_now", return_value=1000.0
        ):
            token = model_access.mint_entry_link("dash")
        # The window is shortened after the link was minted; the link keeps the
        # deadline it was stamped with.
        with gated_gate(ttl_seconds="60"), patch.object(
            model_access, "_now", return_value=1000.0
        ):
            link = model_access.redeem_entry_link(token)
        self.assertEqual(link.expires_at, 1000.0 + 3600)

    def test_expired_link_is_refused(self):
        with gated_gate(ttl_seconds="60"), patch.object(
            model_access, "_now", return_value=1000.0
        ):
            token = model_access.mint_entry_link("dash")
        with gated_gate(), patch.object(model_access, "_now", return_value=5000.0):
            with self.assertRaises(model_access.EntryLinkRefused):
                model_access.redeem_entry_link(token)

    def test_tampered_token_is_refused(self):
        with gated_gate():
            token = model_access.mint_entry_link("dash")
            with self.assertRaises(model_access.EntryLinkRefused):
                model_access.redeem_entry_link(token[:-2] + "xy")

    def test_link_signed_with_another_key_is_refused(self):
        with gated_gate():
            token = model_access.mint_entry_link("dash")
        with gated_gate(signing_key="a-different-signing-key"):
            with self.assertRaises(model_access.EntryLinkRefused):
                model_access.redeem_entry_link(token)

    def test_minting_without_configuration_fails_closed(self):
        unconfigured = {
            gate_config.CREDENTIAL_ENV_VAR: None,
            gate_config.LINK_TTL_ENV_VAR: None,
            gate_config.SIGNING_KEY_NAME: None,
        }
        with patch.object(
            model_access, "gate_configuration", return_value=unconfigured
        ):
            with self.assertRaises(model_access.GateNotConfigured):
                model_access.mint_entry_link("dash")

    def test_committed_signing_key_refuses_to_mint(self):
        """R34: a key published in the repository signs nothing.

        Read through the real configuration path, so the committed
        ``django-insecure-`` default this deployment still carries is what makes
        it fail.
        """
        env = {
            gate_config.CREDENTIAL_ENV_VAR: GATE_CREDENTIAL,
            gate_config.LINK_TTL_ENV_VAR: "3600",
        }
        with patch.dict(os.environ, env):
            with self.assertRaises(model_access.GateNotConfigured):
                model_access.mint_entry_link("dash")


class UnconfiguredGateTests(TestCase):
    """R34: the entry-link route exists everywhere, configured or not.

    The route is registered in every deployment, but the startup check only
    runs when the roster carries a gated model -- so in the shipped default the
    gate is unconfigured and a visitor can still reach the route. It must fail
    closed onto the refusal page rather than a framework error page.
    """

    REFUSAL_MARKER = 'id="model-gate-refused"'

    def _unconfigured(self):
        return patch.object(
            model_access,
            "gate_configuration",
            return_value={
                gate_config.CREDENTIAL_ENV_VAR: None,
                gate_config.LINK_TTL_ENV_VAR: None,
                gate_config.SIGNING_KEY_NAME: None,
            },
        )

    def test_link_presented_to_an_unconfigured_gate_is_refused(self):
        with self._unconfigured():
            resp = self.client.get(gate_url("any-token-at-all"))
        self.assertEqual(resp.status_code, 403)
        self.assertIn(self.REFUSAL_MARKER, resp.content.decode())

    def test_credential_submitted_to_an_unconfigured_gate_is_refused(self):
        # Mint while configured, then present the link to a deployment that is
        # not: the token resolves only if the signing key is still readable.
        with gated_gate(), patch.object(model_access, "_now", return_value=1000.0):
            token = model_access.mint_entry_link("dash")
        with patch.object(model_registry, "MODELS", gated_roster()), patch.object(
            model_access,
            "gate_configuration",
            return_value={
                gate_config.CREDENTIAL_ENV_VAR: None,
                gate_config.LINK_TTL_ENV_VAR: "3600",
                gate_config.SIGNING_KEY_NAME: "test-entry-link-signing-key",
            },
        ), patch.object(model_access, "_now", return_value=1001.0):
            resp = self.client.post(gate_url(token), data={"credential": "anything"})
        self.assertEqual(resp.status_code, 403)
        self.assertIn(self.REFUSAL_MARKER, resp.content.decode())
        self.assertNotIn(model_access.SCOPE_MODEL_KEY, self.client.session)


class SessionIdentityTests(TestCase):
    """Authorization is never written into a session id that predates it."""

    def test_granting_a_scope_rotates_the_session_key(self):
        # Give the client a session before it earns anything.
        session = self.client.session
        session["selected_model_type"] = "1dCNN_z"
        session.save()
        before = self.client.cookies["sessionid"].value

        redeem_scope(self.client, self)

        after = self.client.cookies["sessionid"].value
        self.assertNotEqual(before, after)
        self.assertEqual(self.client.session[model_access.SCOPE_MODEL_KEY], "dash")

    def test_rotation_keeps_an_authenticated_visitor_logged_in(self):
        user = get_user_model().objects.create_user(
            username="reviewer-4", password="not-the-gate-credential"
        )
        self.client.force_login(user)
        redeem_scope(self.client, self)
        self.assertEqual(self.client.session.get("_auth_user_id"), str(user.pk))


class ScopeExpiryEdgeTests(TestCase):
    """The deadline check fails closed on a scope it cannot evaluate."""

    def _session_with(self, deadline):
        session = self.client.session
        session[model_access.SCOPE_MODEL_KEY] = "dash"
        if deadline is not None:
            session[model_access.SCOPE_DEADLINE_KEY] = deadline
        session.save()
        return self.client.session

    def test_scope_without_a_stored_deadline_is_expired(self):
        with patch.object(model_access, "_now", return_value=1000.0):
            self.assertTrue(model_access.scope_expired(self._session_with(None)))

    def test_scope_with_an_unreadable_deadline_is_expired(self):
        with patch.object(model_access, "_now", return_value=1000.0):
            self.assertTrue(model_access.scope_expired(self._session_with("soon")))

    def test_an_unevaluable_scope_refuses_the_classification_view(self):
        self._session_with("soon")
        with gated_gate(), patch.object(model_access, "_now", return_value=1000.0):
            resp = self.client.get(reverse("astrodash:classify"))
        self.assertEqual(resp.status_code, 403)
        self.assertNotIn(model_access.SCOPE_MODEL_KEY, self.client.session)

    def test_a_live_scope_is_not_expired(self):
        with patch.object(model_access, "_now", return_value=1000.0):
            self.assertFalse(model_access.scope_expired(self._session_with(2000.0)))


class CredentialPromptTests(TestCase):
    """R7/R8/AE2/AE11: what an entry link shows, and what a refusal discloses."""

    # A marker the refusal template carries, proving the response is our page
    # and not a framework default error page.
    REFUSAL_MARKER = 'id="model-gate-refused"'
    PROMPT_MARKER = 'id="model-gate-credential"'

    def _mint(self, ttl_seconds="3600", now=1000.0):
        with gated_gate(ttl_seconds=ttl_seconds), patch.object(
            model_access, "_now", return_value=now
        ):
            return model_access.mint_entry_link("dash")

    def test_unexpired_link_renders_the_credential_prompt(self):
        token = self._mint()
        with gated_gate(), patch.object(model_access, "_now", return_value=1001.0):
            resp = self.client.get(gate_url(token))
        self.assertEqual(resp.status_code, 200)
        self.assertIn(self.PROMPT_MARKER, resp.content.decode())

    def test_expired_link_renders_the_refusal_and_names_no_model(self):
        """AE2: refused, and the response reveals nothing about the model."""
        token = self._mint(ttl_seconds="60")
        with gated_gate(), patch.object(model_access, "_now", return_value=9000.0):
            resp = self.client.get(gate_url(token))
        body = resp.content.decode()
        self.assertEqual(resp.status_code, 403)
        self.assertIn(self.REFUSAL_MARKER, body)
        self.assertNotIn(self.PROMPT_MARKER, body)
        self.assertNotIn("dash", body.lower().replace("astrodash", ""))

    def test_tampered_token_is_indistinguishable_from_the_expired_case(self):
        token = self._mint()
        with gated_gate(), patch.object(model_access, "_now", return_value=1001.0):
            tampered = self.client.get(gate_url(token[:-2] + "xy"))
        expired_token = self._mint(ttl_seconds="60")
        with gated_gate(), patch.object(model_access, "_now", return_value=9000.0):
            expired = self.client.get(gate_url(expired_token))
        self.assertEqual(tampered.status_code, expired.status_code)
        self.assertEqual(tampered.content, expired.content)

    def test_correct_credential_scopes_the_session_to_that_model(self):
        token = self._mint()
        with gated_gate(), patch.object(model_access, "_now", return_value=1001.0):
            resp = self.client.post(
                gate_url(token), data={"credential": GATE_CREDENTIAL}
            )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("astrodash:classify"))
        session = self.client.session
        self.assertEqual(session[model_access.SCOPE_MODEL_KEY], "dash")
        self.assertEqual(session[model_access.SCOPE_DEADLINE_KEY], 1000.0 + 3600)

    def test_incorrect_credential_redisplays_the_prompt_and_permits_retry(self):
        """AE11: a mistyped credential on a valid link is retryable."""
        token = self._mint()
        with gated_gate(), patch.object(model_access, "_now", return_value=1001.0):
            wrong = self.client.post(gate_url(token), data={"credential": "not-it"})
            body = wrong.content.decode()
            self.assertEqual(wrong.status_code, 200)
            self.assertIn(self.PROMPT_MARKER, body)
            self.assertNotIn(self.REFUSAL_MARKER, body)
            self.assertNotIn(model_access.SCOPE_MODEL_KEY, self.client.session)
            # The same link still works on the next attempt.
            ok = self.client.post(gate_url(token), data={"credential": GATE_CREDENTIAL})
        self.assertEqual(ok.status_code, 302)
        self.assertEqual(self.client.session[model_access.SCOPE_MODEL_KEY], "dash")

    def test_wrong_credential_error_names_no_model(self):
        token = self._mint()
        with gated_gate(), patch.object(model_access, "_now", return_value=1001.0):
            resp = self.client.post(gate_url(token), data={"credential": "not-it"})
        body = resp.content.decode()
        self.assertNotIn("dash", body.lower().replace("astrodash", ""))

    def test_link_lapsing_between_prompt_and_submit_is_refused(self):
        token = self._mint(ttl_seconds="60")
        with gated_gate(ttl_seconds="60"), patch.object(
            model_access, "_now", return_value=1001.0
        ):
            prompt = self.client.get(gate_url(token))
        self.assertEqual(prompt.status_code, 200)
        with gated_gate(ttl_seconds="60"), patch.object(
            model_access, "_now", return_value=9000.0
        ):
            resp = self.client.post(
                gate_url(token), data={"credential": GATE_CREDENTIAL}
            )
        self.assertEqual(resp.status_code, 403)
        self.assertIn(self.REFUSAL_MARKER, resp.content.decode())
        self.assertNotIn(model_access.SCOPE_MODEL_KEY, self.client.session)

    def test_published_model_link_redirects_into_the_public_flow(self):
        """A reviewer is never locked out of a model that has become public."""
        token = self._mint()
        # No gated roster this time: the model has been published, so its
        # definition no longer requires a credential.
        with gate_configured(), patch.object(model_access, "_now", return_value=1001.0):
            resp = self.client.get(gate_url(token))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("astrodash:classify"))
        session = self.client.session
        self.assertEqual(session["selected_model_type"], "dash")
        self.assertNotIn(model_access.SCOPE_MODEL_KEY, session)


class RedeemClearsPriorStateTests(TestCase):
    """R33/AE15: redeeming starts from a clean session, scope keys written last."""

    def _redeem_over(self, session_state):
        """Seed a session, then redeem a fresh link over it.

        Args:
            session_state: Mapping of session keys to seed before redeeming.

        Returns:
            The session after the redemption.
        """
        session = self.client.session
        session.update(session_state)
        session.save()
        with gated_gate(), patch.object(model_access, "_now", return_value=1000.0):
            token = model_access.mint_entry_link("dash")
            resp = self.client.post(
                gate_url(token), data={"credential": GATE_CREDENTIAL}
            )
        self.assertEqual(resp.status_code, 302)
        return self.client.session

    def test_redeem_clears_a_prior_user_model_selection(self):
        session = self._redeem_over(
            {"selected_model_type": "user_uploaded", "selected_model_id": "um-1"}
        )
        self.assertEqual(session[model_access.SCOPE_MODEL_KEY], "dash")
        self.assertIsNone(session.get("selected_model_id"))

    def test_redeem_clears_prior_classification_artifacts(self):
        session = self._redeem_over(
            {
                "classify_dash_embedding": [0.0] * 1024,
                "classify_results": {"best_matches": []},
                "classify_model_type": "dash",
            }
        )
        for key in (
            "classify_dash_embedding",
            "classify_results",
            "classify_model_type",
        ):
            self.assertNotIn(key, session)


class ScopeDeadlineTests(TestCase):
    """R28/AE10: the stored deadline is absolute and immovable."""

    def test_session_write_after_grant_does_not_move_the_deadline(self):
        with gated_gate(), patch.object(model_access, "_now", return_value=1000.0):
            token = model_access.mint_entry_link("dash")
            self.client.post(gate_url(token), data={"credential": GATE_CREDENTIAL})
            granted = self.client.session[model_access.SCOPE_DEADLINE_KEY]
            # A later request that writes to the session must not re-arm the
            # deadline: an idle-timeout implementation would.
            with patch.object(model_access, "_now", return_value=2000.0):
                self.client.get(reverse("astrodash:classify"))
        self.assertEqual(self.client.session[model_access.SCOPE_DEADLINE_KEY], granted)
        self.assertEqual(granted, 1000.0 + 3600)


class ScopeEnforcementTests(TestCase):
    """R9/R11/R12: every surface that can run a gated model consults the scope."""

    SCOPED_CONTROL_MARKER = 'id="scoped-model-name"'

    def _redeem(self, now=1000.0, ttl_seconds="3600", seed=None):
        """Establish a scope, optionally over seeded session state.

        Args:
            now: The POSIX timestamp to mint and redeem at.
            ttl_seconds: The configured window.
            seed: Optional session state to seed before redeeming.

        Returns:
            The absolute deadline the scope was granted with.
        """
        if seed:
            session = self.client.session
            session.update(seed)
            session.save()
        return redeem_scope(self.client, self, now=now, ttl_seconds=ttl_seconds)

    def _seed_selection(self, **extra):
        session = self.client.session
        session["selected_model_type"] = "dash"
        session.update(extra)
        session.save()

    # --- refused without a scope ---

    def test_gated_classification_view_is_refused_without_a_scope(self):
        self._seed_selection()
        with gated_gate(), patch.object(model_access, "_now", return_value=1000.0):
            resp = self.client.get(reverse("astrodash:classify"))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            resp["Location"].startswith(reverse("astrodash:model_selection")),
            resp["Location"],
        )

    def test_gated_surface_page_route_is_refused_without_a_scope(self):
        self._seed_selection()
        with gated_gate(), patch.object(model_access, "_now", return_value=1000.0):
            resp = self.client.get(reverse("astrodash:dash_twins"))
        self.assertEqual(resp.status_code, 403)

    def test_gated_surface_data_route_is_refused_without_a_scope(self):
        self._seed_selection()
        with gated_gate(), patch.object(model_access, "_now", return_value=1000.0):
            resp = self.client.get(reverse("astrodash:dash_twins_data"))
        self.assertEqual(resp.status_code, 403)

    def test_gated_surface_search_route_is_refused_without_a_scope(self):
        self._seed_selection(
            classify_model_type="dash", classify_dash_embedding=[0.0] * 1024
        )
        with gated_gate(), patch.object(model_access, "_now", return_value=1000.0):
            resp = self.client.get(reverse("astrodash:twins_search"))
        self.assertEqual(resp.status_code, 403)

    # --- the model control in a scoped session ---

    def test_scoped_session_renders_the_model_control_disabled_and_named(self):
        self._redeem()
        with gated_gate(), patch.object(model_access, "_now", return_value=1001.0):
            resp = self.client.get(reverse("astrodash:classify"))
        body = resp.content.decode()
        self.assertEqual(resp.status_code, 200)
        self.assertIn(self.SCOPED_CONTROL_MARKER, body)
        control = re.search(r"<input[^>]*id=\"scoped-model-name\"[^>]*>", body).group(0)
        self.assertIn("disabled", control)
        self.assertIn(model_registry.get_definition("dash").title, control)
        # The dropdown is not offered alongside it.
        self.assertNotIn('id="id_model"', body)

    def test_scoped_page_leaks_no_template_comment_text(self):
        """A ``{# #}`` comment that spans lines is rendered, not stripped."""
        self._redeem()
        with gated_gate(), patch.object(model_access, "_now", return_value=1001.0):
            resp = self.client.get(reverse("astrodash:classify"))
        body = resp.content.decode()
        self.assertEqual(resp.status_code, 200)
        for delimiter in ("{#", "#}"):
            self.assertNotIn(
                delimiter,
                body,
                "template comment delimiters reached the rendered page",
            )

    # --- the model that actually runs ---

    def test_scoped_submission_validates_though_the_model_is_unlisted(self):
        """A gated model is unlisted, so its id is in no listed choice set."""
        self._redeem()
        with gated_gate(), patch.object(
            model_access, "_now", return_value=1001.0
        ), mocked_classification("dash") as classification_svc:
            self.client.post(reverse("astrodash:classify"), data=classify_post("dash"))
        self.assertEqual(
            classification_svc.classify_spectrum.await_args.kwargs["model_type"], "dash"
        )

    def test_altered_request_still_runs_the_scoped_model(self):
        """AE3: the request cannot be moved to another model."""
        self._redeem()
        with gated_gate(), patch.object(
            model_access, "_now", return_value=1001.0
        ), mocked_classification("dash") as classification_svc:
            self.client.post(
                reverse("astrodash:classify"), data=classify_post("1dCNN_z")
            )
        self.assertEqual(
            classification_svc.classify_spectrum.await_args.kwargs["model_type"], "dash"
        )

    def test_prior_uploaded_selection_does_not_survive_into_the_scope(self):
        """AE15: a stale selection cannot displace the scope."""
        self._redeem(
            seed={"selected_model_type": "user_uploaded", "selected_model_id": "um-1"}
        )
        with gated_gate(), patch.object(
            model_access, "_now", return_value=1001.0
        ), mocked_classification("dash") as classification_svc:
            self.client.post(reverse("astrodash:classify"), data=classify_post("dash"))
        kwargs = classification_svc.classify_spectrum.await_args.kwargs
        self.assertEqual(kwargs["model_type"], "dash")
        self.assertIsNone(kwargs["user_model_id"])

    # --- the deadline is a lifetime, not an idle timeout ---

    def test_continuous_classification_still_loses_access_at_the_deadline(self):
        """AE10: activity must not extend the deadline."""
        deadline = self._redeem(now=1000.0, ttl_seconds="3600")
        for tick in (1001.0, 2000.0, 3000.0, 4000.0):
            with gated_gate(), patch.object(
                model_access, "_now", return_value=tick
            ), mocked_classification("dash"):
                resp = self.client.post(
                    reverse("astrodash:classify"), data=classify_post("dash")
                )
            self.assertEqual(resp.status_code, 200, f"refused early at {tick}")
            self.assertEqual(
                self.client.session[model_access.SCOPE_DEADLINE_KEY], deadline
            )
        with gated_gate(), patch.object(
            model_access, "_now", return_value=deadline + 1
        ):
            resp = self.client.get(reverse("astrodash:classify"))
        self.assertEqual(resp.status_code, 403)
        self.assertIn(CredentialPromptTests.REFUSAL_MARKER, resp.content.decode())
        self.assertNotIn(model_access.SCOPE_MODEL_KEY, self.client.session)

    def test_lapsed_scope_refusal_names_no_model(self):
        deadline = self._redeem()
        with gated_gate(), patch.object(
            model_access, "_now", return_value=deadline + 1
        ):
            resp = self.client.get(reverse("astrodash:classify"))
        body = resp.content.decode()
        self.assertNotIn("dash", body.lower().replace("astrodash", ""))

    def test_scope_survives_django_login_with_its_deadline_intact(self):
        deadline = self._redeem()
        user = get_user_model().objects.create_user(
            username="reviewer", password="not-the-gate-credential"
        )
        self.client.force_login(user)
        session = self.client.session
        self.assertEqual(session[model_access.SCOPE_MODEL_KEY], "dash")
        self.assertEqual(session[model_access.SCOPE_DEADLINE_KEY], deadline)
        with gated_gate(), patch.object(model_access, "_now", return_value=1001.0):
            resp = self.client.get(reverse("astrodash:classify"))
        self.assertEqual(resp.status_code, 200)


class ScopeBoundaryTests(TestCase):
    """R24/R29/R33/R35: what a scope may reach, and what ending it discards."""

    END_CONTROL_MARKER = 'id="end-scope-button"'

    def _redeem(self, now=1000.0, ttl_seconds="3600"):
        return redeem_scope(self.client, self, now=now, ttl_seconds=ttl_seconds)

    # --- a scope reaches classification only ---

    def test_batch_flow_is_refused_for_a_scoped_session(self):
        """AE8: a scoped session may enter the classification flow only."""
        self._redeem()
        with gated_gate(), patch.object(model_access, "_now", return_value=1001.0):
            resp = self.client.get(reverse("astrodash:batch_process_ui"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("astrodash:classify"))

    def test_batch_refusal_surfaces_the_end_action(self):
        self._redeem()
        with gated_gate(), patch.object(model_access, "_now", return_value=1001.0):
            resp = self.client.get(reverse("astrodash:batch_process_ui"), follow=True)
        self.assertIn(self.END_CONTROL_MARKER, resp.content.decode())

    def test_selection_page_is_refused_for_a_scoped_session(self):
        self._redeem()
        model_svc = MagicMock(list_models=AsyncMock(return_value=[]))
        with gated_gate(), patch.object(
            model_access, "_now", return_value=1001.0
        ), patch("astrodash.ui_views.get_model_service", return_value=model_svc):
            resp = self.client.get(reverse("astrodash:model_selection"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("astrodash:classify"))

    def test_selection_post_is_refused_for_a_scoped_session(self):
        self._redeem()
        model_svc = MagicMock(list_models=AsyncMock(return_value=[]))
        with gated_gate(), patch.object(
            model_access, "_now", return_value=1001.0
        ), patch("astrodash.ui_views.get_model_service", return_value=model_svc):
            resp = self.client.post(
                reverse("astrodash:model_selection"),
                data={"model_type": "1dCNN_z", "action_type": "classify"},
            )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("astrodash:classify"))
        session = self.client.session
        self.assertEqual(session[model_access.SCOPE_MODEL_KEY], "dash")
        # The POST did not move the selection either: a refused selection that
        # still wrote the session would let a scope run a model it does not name.
        self.assertEqual(session["selected_model_type"], "dash")

    # --- the explicit end action ---

    def test_end_control_renders_on_a_guarded_page_load(self):
        self._redeem()
        with gated_gate(), patch.object(model_access, "_now", return_value=1001.0):
            resp = self.client.get(reverse("astrodash:classify"))
        self.assertIn(self.END_CONTROL_MARKER, resp.content.decode())

    def test_ending_the_scope_returns_to_the_public_picker(self):
        self._redeem()
        with gated_gate(), patch.object(model_access, "_now", return_value=1001.0):
            resp = self.client.post(reverse("astrodash:end_model_scope"))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            resp["Location"].startswith(reverse("astrodash:model_selection")),
            resp["Location"],
        )
        self.assertNotIn(model_access.SCOPE_MODEL_KEY, self.client.session)

    def test_ending_the_scope_discards_what_it_produced(self):
        """AE12: a later twins request cannot be served from a retained embedding."""
        self._redeem()
        with gated_gate(), patch.object(
            model_access, "_now", return_value=1001.0
        ), mocked_classification("dash"):
            self.client.post(reverse("astrodash:classify"), data=classify_post("dash"))
        self.assertEqual(
            self.client.session.get("classify_dash_embedding"), [0.0] * 1024
        )
        with gated_gate(), patch.object(model_access, "_now", return_value=1002.0):
            self.client.post(reverse("astrodash:end_model_scope"))
            resp = self.client.get(reverse("astrodash:twins_search"))
        self.assertEqual(resp.status_code, 403)
        self.assertNotIn("classify_dash_embedding", self.client.session)

    def test_teardown_leaves_no_artifact_or_selection_key(self):
        self._redeem()
        session = self.client.session
        session["classify_results"] = {"best_matches": []}
        session["classify_model_type"] = "dash"
        session.save()
        with gated_gate(), patch.object(model_access, "_now", return_value=1001.0):
            self.client.post(reverse("astrodash:end_model_scope"))
        session = self.client.session
        survivors = [k for k in session.keys() if k.startswith("classify_")]
        self.assertEqual(survivors, [])
        for key in (
            model_access.SELECTION_SESSION_KEYS + model_access.SCOPE_SESSION_KEYS
        ):
            self.assertNotIn(key, session)

    def test_teardown_leaves_an_authenticated_visitor_logged_in(self):
        user = get_user_model().objects.create_user(
            username="reviewer-2", password="not-the-gate-credential"
        )
        self.client.force_login(user)
        self._redeem()
        with gated_gate(), patch.object(model_access, "_now", return_value=1001.0):
            self.client.post(reverse("astrodash:end_model_scope"))
        self.assertEqual(self.client.session.get("_auth_user_id"), str(user.pk))

    def test_end_action_is_idempotent_when_unscoped(self):
        resp = self.client.post(reverse("astrodash:end_model_scope"))
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn(model_access.SCOPE_MODEL_KEY, self.client.session)

    def test_logout_leaves_no_scope_and_no_artifact_behind(self):
        user = get_user_model().objects.create_user(
            username="reviewer-3", password="not-the-gate-credential"
        )
        self.client.force_login(user)
        self._redeem()
        with gated_gate(), patch.object(
            model_access, "_now", return_value=1001.0
        ), mocked_classification("dash"):
            self.client.post(reverse("astrodash:classify"), data=classify_post("dash"))
        self.client.logout()
        session = self.client.session
        self.assertNotIn(model_access.SCOPE_MODEL_KEY, session)
        self.assertEqual([k for k in session.keys() if k.startswith("classify_")], [])

    # --- revalidation on entry ---

    def test_public_session_whose_model_became_gated_is_turned_away(self):
        """AE14: a stale selection cannot reach the gate."""
        session = self.client.session
        session["selected_model_type"] = "dash"
        session.save()
        with gated_gate(), patch.object(model_access, "_now", return_value=1000.0):
            resp = self.client.get(reverse("astrodash:classify"))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            resp["Location"].startswith(reverse("astrodash:model_selection")),
            resp["Location"],
        )
        self.assertIsNone(self.client.session.get("selected_model_type"))

    def test_public_session_whose_model_became_unlisted_is_turned_away_from_batch(self):
        session = self.client.session
        session["selected_model_type"] = "dash"
        session.save()
        unlisted = tuple(
            replace(m, listed=False) if m.id == "dash" else m
            for m in promote_stand_in_default(model_registry.MODELS, "dash")
        )
        model_registry.validate_registry(unlisted)
        with patch.object(model_registry, "MODELS", unlisted):
            resp = self.client.get(reverse("astrodash:batch_process_ui"))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            resp["Location"].startswith(reverse("astrodash:model_selection")),
            resp["Location"],
        )

    def test_scope_whose_model_was_retired_is_released(self):
        """R35: retirement makes a model unselectable, scope or no scope."""
        self._redeem()
        retired = tuple(
            replace(m, status=model_registry.STATUS_RETIRED) if m.id == "dash" else m
            for m in gated_roster()
        )
        with patch.object(
            model_registry, "MODELS", retired
        ), gate_configured(), patch.object(model_access, "_now", return_value=1001.0):
            resp = self.client.get(reverse("astrodash:classify"))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            resp["Location"].startswith(reverse("astrodash:model_selection")),
            resp["Location"],
        )
        self.assertNotIn(model_access.SCOPE_MODEL_KEY, self.client.session)

    def test_mint_command_refuses_a_retired_gated_model(self):
        retired = tuple(
            replace(m, status=model_registry.STATUS_RETIRED) if m.id == "dash" else m
            for m in gated_roster()
        )
        with patch.object(model_registry, "MODELS", retired), gate_configured():
            with self.assertRaises(CommandError):
                call_command("mint_model_link", "dash", stdout=StringIO())

    def test_scoped_session_reaches_twins_search_after_classifying(self):
        """The scoped happy path, not only its refusals."""
        self._redeem()
        with gated_gate(), patch.object(
            model_access, "_now", return_value=1001.0
        ), mocked_classification("dash"):
            self.client.post(reverse("astrodash:classify"), data=classify_post("dash"))
        twins_svc = MagicMock(
            find_twins=MagicMock(
                return_value={"twin_indices": [1], "twin_similarities": [0.9]}
            )
        )
        with gated_gate(), patch.object(
            model_access, "_now", return_value=1002.0
        ), patch("astrodash.ui_views.get_twins_search_service", return_value=twins_svc):
            resp = self.client.get(reverse("astrodash:twins_search"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["twin_indices"], [1])

    def test_scope_whose_model_was_published_dissolves_on_the_next_request(self):
        self._redeem()
        # No gated roster: the model has been published since the scope began.
        with gate_configured(), patch.object(model_access, "_now", return_value=1001.0):
            resp = self.client.get(reverse("astrodash:classify"))
        session = self.client.session
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(model_access.SCOPE_MODEL_KEY, session)
        self.assertEqual(session.get("selected_model_type"), "dash")
        self.assertNotIn(
            ScopeEnforcementTests.SCOPED_CONTROL_MARKER, resp.content.decode()
        )

    def test_user_uploaded_session_passes_revalidation_untouched(self):
        session = self.client.session
        session["selected_model_type"] = "user_uploaded"
        session["selected_model_id"] = "um-1"
        session.save()
        model_svc = MagicMock(
            get_model=AsyncMock(return_value=SimpleNamespace(name="My model"))
        )
        with patch("astrodash.ui_views.get_model_service", return_value=model_svc):
            classify_resp = self.client.get(reverse("astrodash:classify"))
            batch_resp = self.client.get(reverse("astrodash:batch_process_ui"))
        self.assertEqual(classify_resp.status_code, 200)
        self.assertEqual(batch_resp.status_code, 200)
        self.assertEqual(self.client.session["selected_model_type"], "user_uploaded")

    # --- the two refusals speak to different audiences ---

    def test_lapsed_refusal_says_the_window_ended_while_a_cold_link_does_not(self):
        deadline = self._redeem()
        with gated_gate(), patch.object(
            model_access, "_now", return_value=deadline + 1
        ):
            lapsed = self.client.get(reverse("astrodash:classify"))
        with gated_gate(), patch.object(model_access, "_now", return_value=1000.0):
            cold = self.client.get(gate_url("not-a-real-token"))
        lapsed_body = lapsed.content.decode()
        cold_body = cold.content.decode()
        self.assertIn(model_access.SCOPE_LAPSED_HEADING, lapsed_body)
        self.assertNotIn(model_access.SCOPE_LAPSED_HEADING, cold_body)


class UngatedFlowUnaffectedTests(TestCase):
    """R21: with no gated model anywhere, nothing about the gate is visible."""

    def test_public_classify_page_still_renders_the_model_dropdown(self):
        session = self.client.session
        session["selected_model_type"] = "dash"
        session.save()
        resp = self.client.get(reverse("astrodash:classify"))
        body = resp.content.decode()
        self.assertEqual(resp.status_code, 200)
        self.assertIn('id="id_model"', body)
        self.assertNotIn(ScopeEnforcementTests.SCOPED_CONTROL_MARKER, body)

    def test_user_uploaded_model_still_classifies_unchanged(self):
        """R23/AE7: the gate work leaves the uploaded-model path alone."""
        session = self.client.session
        session["selected_model_type"] = "user_uploaded"
        session["selected_model_id"] = "um-1"
        session.save()
        model_svc = MagicMock(
            get_model=AsyncMock(return_value=SimpleNamespace(name="My model"))
        )
        with patch(
            "astrodash.ui_views.get_model_service", return_value=model_svc
        ), mocked_classification("user_uploaded") as classification_svc:
            self.client.post(
                reverse("astrodash:classify"), data=classify_post("user_uploaded")
            )
        kwargs = classification_svc.classify_spectrum.await_args.kwargs
        self.assertEqual(kwargs["model_type"], "user_uploaded")
        self.assertEqual(kwargs["user_model_id"], "um-1")
        # A model the registry cannot resolve declares no twins surface, so
        # nothing is stashed for it -- exactly as before.
        self.assertNotIn("classify_dash_embedding", self.client.session)

    def test_public_twins_routes_still_served_for_dash(self):
        session = self.client.session
        session["selected_model_type"] = "dash"
        session.save()
        self.assertEqual(
            self.client.get(reverse("astrodash:dash_twins")).status_code, 200
        )


class RetiredTransformerSessionTests(TestCase):
    """R7: a session still holding the retired Transformer cannot classify with it.

    These run against the real roster, where the Transformer is retired, rather
    than a patched one: the point is that the shipped registry turns it away.
    """

    def setUp(self):
        session = self.client.session
        session["selected_model_type"] = "transformer"
        session.save()

    def assert_sent_back_to_the_picker(self, resp) -> None:
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp["Location"], reverse("astrodash:model_selection") + "?action=classify"
        )
        self.assertIn(
            model_access.MODEL_NO_LONGER_SELECTABLE_MESSAGE,
            [str(m) for m in get_messages(resp.wsgi_request)],
        )
        self.assertIsNone(self.client.session.get("selected_model_type"))

    def test_stale_session_get_is_sent_back_to_the_picker(self):
        resp = self.client.get(reverse("astrodash:classify"))
        self.assert_sent_back_to_the_picker(resp)

    def test_stale_session_post_is_sent_back_and_nothing_is_classified(self):
        with mocked_classification("transformer") as classification_svc:
            resp = self.client.post(
                reverse("astrodash:classify"), data=classify_post("transformer")
            )
        self.assert_sent_back_to_the_picker(resp)
        classification_svc.classify_spectrum.assert_not_awaited()

    def test_selecting_the_transformer_is_refused_as_an_invalid_choice(self):
        session = self.client.session
        session["selected_model_type"] = "dash"
        session.save()
        model_svc = MagicMock(list_models=AsyncMock(return_value=[]))
        with patch("astrodash.ui_views.get_model_service", return_value=model_svc):
            resp = self.client.post(
                reverse("astrodash:model_selection"),
                data={"model_type": "transformer", "action_type": "classify"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("model_type", resp.context["form"].errors)
        self.assertEqual(self.client.session["selected_model_type"], "dash")


class MintCommandTests(TestCase):
    """R10: minting is operator-only, and no route mints a link."""

    def _mint_via_command(self, *args):
        out = StringIO()
        with gated_gate(), patch.object(model_access, "_now", return_value=1000.0):
            call_command("mint_model_link", *args, stdout=out)
        return out.getvalue().strip()

    def test_command_prints_a_redeemable_link(self):
        printed = self._mint_via_command("dash")
        self.assertTrue(printed.startswith(GATE_LINK_BASE_URL), printed)
        path = printed[len(GATE_LINK_BASE_URL) :]
        with gated_gate(), patch.object(model_access, "_now", return_value=1001.0):
            resp = self.client.get(path)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(CredentialPromptTests.PROMPT_MARKER, resp.content.decode())

    def test_command_honors_an_explicit_window(self):
        printed = self._mint_via_command("dash", "--ttl-seconds", "120")
        token = printed.rstrip("/").rsplit("/", 1)[-1]
        with gated_gate(), patch.object(model_access, "_now", return_value=1000.0):
            link = model_access.redeem_entry_link(token)
        self.assertEqual(link.expires_at, 1000.0 + 120)

    def test_command_refuses_an_ungated_model(self):
        with gate_configured():
            with self.assertRaises(CommandError):
                call_command("mint_model_link", "1dCNN_z", stdout=StringIO())

    def test_command_refuses_an_unknown_model(self):
        with gated_gate():
            with self.assertRaises(CommandError):
                call_command("mint_model_link", "no-such-model", stdout=StringIO())

    def test_no_url_route_mints_a_link(self):
        from astrodash import urls as astrodash_urls

        names = [p.name for p in astrodash_urls.urlpatterns]
        self.assertNotIn("mint_model_link", names)
        for name in names:
            self.assertNotIn("mint", name)
        # The gate route exists, and it is the redeem side only.
        self.assertIn("model_gate", names)

    def test_command_without_a_link_base_url_fails_closed(self):
        with gated_gate():
            os.environ.pop(gate_config.LINK_BASE_URL_ENV_VAR, None)
            with self.assertRaises(CommandError):
                call_command("mint_model_link", "dash", stdout=StringIO())


class LinkBaseUrlTests(TestCase):
    """The mint command's host is normalized, since it is pasted into a link."""

    def _base_url(self, value):
        with patch.dict(os.environ, {gate_config.LINK_BASE_URL_ENV_VAR: value}):
            return gate_config.link_base_url()

    def test_trailing_slash_is_stripped(self):
        self.assertEqual(
            self._base_url("https://astrodash-dev.example.org/"),
            "https://astrodash-dev.example.org",
        )

    def test_surrounding_whitespace_is_stripped(self):
        self.assertEqual(
            self._base_url("  https://astrodash-dev.example.org  "),
            "https://astrodash-dev.example.org",
        )

    def test_a_blank_value_is_unconfigured(self):
        self.assertIsNone(self._base_url("   "))


class SessionCookieSecurityTests(TestCase):
    """The session now carries authorization, not just a preference."""

    def test_session_cookie_is_marked_secure(self):
        from django.conf import settings

        self.assertTrue(settings.SESSION_COOKIE_SECURE)
        # Pinned alongside the CSRF cookie's flag, which was already set.
        self.assertTrue(settings.CSRF_COOKIE_SECURE)


class RefusalCopyTests(TestCase):
    """The refusal is the first thing an anonymous reviewer may ever see."""

    def test_refusal_is_not_a_framework_default_error_page(self):
        with gated_gate():
            resp = self.client.get(gate_url("not-a-real-token"))
        body = resp.content.decode()
        self.assertEqual(resp.status_code, 403)
        self.assertIn(CredentialPromptTests.REFUSAL_MARKER, body)
        # Rendered through the site's own layout, not Django's bare 403 page.
        self.assertNotIn("Forbidden</h1>", body)
        self.assertTrue(re.search(r"<html", body, re.IGNORECASE))
