"""Unit tests for the central model registry (plan U2).

These pin the registry's own contract -- ordering, default resolution,
capability fields, retirement, and the exactly-one-default invariant -- before
any read site consumes it. They are pure in-memory tests: no database, no model
weights.
"""

import os
from dataclasses import replace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from astrodash import surfaces
from astrodash.core import gate_config
from astrodash.infrastructure.ml import model_registry as registry
from astrodash.infrastructure.ml.classifiers.dash_classifier import DashClassifier
from astrodash.infrastructure.ml.classifiers.transformer_classifier import (
    TransformerClassifier,
)


class RegistryOrderingTests(SimpleTestCase):
    def test_active_definitions_are_transformer_then_dash(self):
        ids = [d.id for d in registry.active_definitions()]
        self.assertEqual(
            ids,
            [
                "transformer",
                "dash",
                "1dCNN_z",
                "1dCNN_noz",
                "latent_z",
                "latent_noz",
            ],
        )

    def test_default_is_transformer(self):
        self.assertEqual(registry.default_definition().id, "transformer")

    def test_listed_definitions_include_dash_transformer_and_website_final(self):
        ids = [d.id for d in registry.listed_definitions()]
        self.assertEqual(
            ids,
            [
                "transformer",
                "dash",
                "1dCNN_z",
                "1dCNN_noz",
                "latent_z",
                "latent_noz",
            ],
        )


class DefinitionFieldsTests(SimpleTestCase):
    def test_dash_capability_fields(self):
        dash = registry.get_definition("dash")
        self.assertIsNotNone(dash)
        self.assertEqual(dash.redshift_input, registry.REDSHIFT_INPUT_OPTIONAL)
        self.assertIn(registry.SURFACE_DASH_TWINS, dash.surfaces)
        self.assertTrue(dash.supports_redshift_estimation)
        self.assertTrue(dash.supports_template_overlays)
        self.assertTrue(dash.supports_rlap)
        self.assertEqual(dash.preprocessing, "dash")
        self.assertTrue(dash.recommended)
        self.assertIs(dash.classifier, DashClassifier)

    def test_transformer_capability_fields(self):
        tr = registry.get_definition("transformer")
        self.assertIsNotNone(tr)
        self.assertEqual(tr.redshift_input, registry.REDSHIFT_INPUT_REQUIRED)
        self.assertNotIn(registry.SURFACE_DASH_TWINS, tr.surfaces)
        self.assertFalse(tr.supports_redshift_estimation)
        self.assertFalse(tr.supports_template_overlays)
        self.assertFalse(tr.supports_rlap)
        self.assertEqual(tr.preprocessing, "transformer")
        self.assertTrue(tr.is_default)
        self.assertIs(tr.classifier, TransformerClassifier)

    def test_website_final_capability_fields(self):
        expected = (
            ("1dCNN_z", "1dcnn", registry.REDSHIFT_INPUT_REQUIRED),
            ("1dCNN_noz", "1dcnn", registry.REDSHIFT_INPUT_NONE),
            ("latent_z", "latent", registry.REDSHIFT_INPUT_REQUIRED),
            ("latent_noz", "latent", registry.REDSHIFT_INPUT_NONE),
        )
        for model_id, preprocessing, redshift_input in expected:
            with self.subTest(model=model_id):
                definition = registry.get_definition(model_id)
                self.assertIsNotNone(definition)
                self.assertEqual(definition.preprocessing, preprocessing)
                self.assertEqual(definition.redshift_input, redshift_input)
                self.assertEqual(
                    list(definition.surfaces), [registry.SURFACE_CLASSIFICATION]
                )
                self.assertFalse(definition.supports_redshift_estimation)
                self.assertFalse(definition.supports_template_overlays)
                self.assertFalse(definition.supports_rlap)
                self.assertFalse(definition.is_default)
                self.assertTrue(definition.listed)
                self.assertFalse(definition.requires_credential)

    def test_unknown_id_resolves_to_none(self):
        self.assertIsNone(registry.get_definition("user_uploaded"))
        self.assertIsNone(registry.get_definition("nope"))


class RetirementTests(SimpleTestCase):
    """AE5: retiring a model hides it from active surfaces but still resolves it."""

    def test_retired_model_drops_from_active_but_still_resolves(self):
        transformer = registry.get_definition("transformer")
        dash = registry.get_definition("dash")
        # Retire Transformer and promote DASH to default so the invariant holds.
        retired_transformer = replace(transformer, status=registry.STATUS_RETIRED)
        promoted_dash = replace(dash, is_default=True)
        patched = (retired_transformer, promoted_dash)

        with patch.object(registry, "MODELS", patched):
            active_ids = [d.id for d in registry.active_definitions()]
            self.assertEqual(active_ids, ["dash"])
            self.assertEqual(registry.default_definition().id, "dash")
            # Still resolvable for label/field lookups by any stored result.
            still = registry.get_definition("transformer")
            self.assertIsNotNone(still)
            self.assertEqual(still.title, "Transformer Model")
            self.assertFalse(still.is_active)


class InvariantTests(SimpleTestCase):
    def test_two_active_defaults_raises(self):
        transformer = registry.get_definition("transformer")
        dash = registry.get_definition("dash")
        both_default = (transformer, replace(dash, is_default=True))
        with self.assertRaises(ValueError):
            registry.validate_registry(both_default)

    def test_zero_active_defaults_raises(self):
        transformer = registry.get_definition("transformer")
        dash = registry.get_definition("dash")
        none_default = (replace(transformer, is_default=False), dash)
        with self.assertRaises(ValueError):
            registry.validate_registry(none_default)

    def test_duplicate_ids_raise(self):
        transformer = registry.get_definition("transformer")
        with self.assertRaises(ValueError):
            registry.validate_registry((transformer, transformer))

    def test_production_registry_is_valid(self):
        # Should not raise.
        registry.validate_registry(registry.MODELS)

    def test_gated_and_listed_raises(self):
        """R26: a model requiring a credential may never be listed."""
        transformer = registry.get_definition("transformer")
        dash = registry.get_definition("dash")
        gated_but_listed = replace(dash, requires_credential=True, listed=True)
        with self.assertRaises(ValueError) as ctx:
            registry.validate_registry((transformer, gated_but_listed))
        self.assertIn("dash", str(ctx.exception))

    def test_gated_default_raises(self):
        """R26: the active default may never require a credential."""
        transformer = registry.get_definition("transformer")
        gated_default = replace(transformer, requires_credential=True, listed=False)
        with self.assertRaises(ValueError) as ctx:
            registry.validate_registry((gated_default,))
        self.assertIn("transformer", str(ctx.exception))

    def test_unlisted_default_raises(self):
        """R26: the active default is always listed."""
        transformer = registry.get_definition("transformer")
        dash = registry.get_definition("dash")
        unlisted_default = replace(transformer, listed=False)
        with self.assertRaises(ValueError) as ctx:
            registry.validate_registry((unlisted_default, dash))
        self.assertIn("transformer", str(ctx.exception))


class ListingTests(SimpleTestCase):
    """R1/KD1: listing is independent of lifecycle status."""

    def test_unlisted_active_model_drops_from_listed_only(self):
        transformer = registry.get_definition("transformer")
        dash = registry.get_definition("dash")
        unlisted_dash = replace(dash, listed=False)
        patched = (transformer, unlisted_dash)

        with patch.object(registry, "MODELS", patched):
            self.assertEqual(
                [d.id for d in registry.active_definitions()],
                ["transformer", "dash"],
            )
            self.assertEqual(
                [d.id for d in registry.listed_definitions()],
                ["transformer"],
            )

    def test_retired_model_drops_from_active_and_listed(self):
        transformer = registry.get_definition("transformer")
        dash = registry.get_definition("dash")
        retired_dash = replace(dash, status=registry.STATUS_RETIRED)
        patched = (transformer, retired_dash)

        with patch.object(registry, "MODELS", patched):
            self.assertEqual(
                [d.id for d in registry.active_definitions()], ["transformer"]
            )
            self.assertEqual(
                [d.id for d in registry.listed_definitions()], ["transformer"]
            )

    def test_builtin_models_are_listed_and_ungated(self):
        for model_id in (
            "dash",
            "transformer",
            "1dCNN_z",
            "1dCNN_noz",
            "latent_z",
            "latent_noz",
        ):
            with self.subTest(model=model_id):
                definition = registry.get_definition(model_id)
                self.assertTrue(definition.listed)
                self.assertFalse(definition.requires_credential)


class RedshiftInputPolicyTests(SimpleTestCase):
    """R13/R15/KD5: redshift input is a three-way policy, and the sole authority."""

    def test_builtin_policies_match_todays_semantics(self):
        dash = registry.get_definition("dash")
        transformer = registry.get_definition("transformer")
        self.assertEqual(dash.redshift_input, registry.REDSHIFT_INPUT_OPTIONAL)
        self.assertEqual(transformer.redshift_input, registry.REDSHIFT_INPUT_REQUIRED)
        self.assertEqual(
            registry.get_definition("1dCNN_z").redshift_input,
            registry.REDSHIFT_INPUT_REQUIRED,
        )
        self.assertEqual(
            registry.get_definition("latent_z").redshift_input,
            registry.REDSHIFT_INPUT_REQUIRED,
        )
        self.assertEqual(
            registry.get_definition("1dCNN_noz").redshift_input,
            registry.REDSHIFT_INPUT_NONE,
        )
        self.assertEqual(
            registry.get_definition("latent_noz").redshift_input,
            registry.REDSHIFT_INPUT_NONE,
        )

    def test_the_three_policies_are_distinct(self):
        self.assertEqual(
            len(
                {
                    registry.REDSHIFT_INPUT_REQUIRED,
                    registry.REDSHIFT_INPUT_OPTIONAL,
                    registry.REDSHIFT_INPUT_NONE,
                }
            ),
            3,
        )

    def test_no_derived_redshift_boolean_remains(self):
        """KTD12: the boolean the policy replaced is gone, not shadowing it."""
        dash = registry.get_definition("dash")
        self.assertFalse(hasattr(dash, "requires_redshift"))

    def test_declining_redshift_is_not_requiring_it(self):
        dash = registry.get_definition("dash")
        no_redshift = replace(dash, redshift_input=registry.REDSHIFT_INPUT_NONE)
        self.assertNotEqual(
            no_redshift.redshift_input, registry.REDSHIFT_INPUT_REQUIRED
        )
        # R15/AE5: declining redshift as an input says nothing about estimating
        # one -- the model still produces a redshift estimate.
        self.assertTrue(no_redshift.supports_redshift_estimation)


class SurfaceDeclarationTests(SimpleTestCase):
    """R16/R18/R31/KD4: result surfaces are a declared, ordered list."""

    def test_dash_declares_classification_then_dash_twins(self):
        dash = registry.get_definition("dash")
        self.assertEqual(
            list(dash.surfaces),
            [registry.SURFACE_CLASSIFICATION, registry.SURFACE_DASH_TWINS],
        )

    def test_transformer_declares_classification_only(self):
        transformer = registry.get_definition("transformer")
        self.assertEqual(list(transformer.surfaces), [registry.SURFACE_CLASSIFICATION])

    def test_declared_surfaces_resolve_in_declared_order(self):
        """AE7: DASH resolves both surfaces in order, Transformer only one."""
        dash = registry.get_definition("dash")
        transformer = registry.get_definition("transformer")
        self.assertEqual(
            [s.title for s in surfaces.resolve_surfaces(dash.surfaces)],
            ["Classification", "DASH Twins"],
        )
        self.assertEqual(
            [s.title for s in surfaces.resolve_surfaces(transformer.surfaces)],
            ["Classification"],
        )

    def test_declared_list_is_the_sole_authority_for_twins(self):
        """R31: the declared list is the only place twins is answered.

        The transitional ``supports_twins`` property is gone (KTD12: U5 owned
        its read site and deleted it), so membership in ``surfaces`` is the
        whole answer -- and flipping the list flips it.
        """
        self.assertFalse(hasattr(registry.get_definition("dash"), "supports_twins"))

        transformer = registry.get_definition("transformer")
        gains_twins = replace(
            transformer,
            surfaces=(registry.SURFACE_CLASSIFICATION, registry.SURFACE_DASH_TWINS),
        )
        self.assertIn(registry.SURFACE_DASH_TWINS, gains_twins.surfaces)

        dash = registry.get_definition("dash")
        loses_twins = replace(dash, surfaces=(registry.SURFACE_CLASSIFICATION,))
        self.assertNotIn(registry.SURFACE_DASH_TWINS, loses_twins.surfaces)

    def test_capability_booleans_are_independent_of_the_surface_list(self):
        """R30/KTD11: overlay, RLap, and redshift-estimation stay booleans."""
        dash = registry.get_definition("dash")
        no_twins_surface = replace(dash, surfaces=(registry.SURFACE_CLASSIFICATION,))
        self.assertNotIn(registry.SURFACE_DASH_TWINS, no_twins_surface.surfaces)
        self.assertTrue(no_twins_surface.supports_redshift_estimation)
        self.assertTrue(no_twins_surface.supports_template_overlays)
        self.assertTrue(no_twins_surface.supports_rlap)


class SurfaceValidationTests(SimpleTestCase):
    """R16/R17: a declared surface list is validated against the known ids."""

    def _known_ids(self):
        return surfaces.known_surface_ids()

    def test_unknown_surface_id_is_rejected(self):
        transformer = registry.get_definition("transformer")
        dash = registry.get_definition("dash")
        bogus = replace(
            dash, surfaces=(registry.SURFACE_CLASSIFICATION, "no_such_surface")
        )
        with self.assertRaises(ValueError) as ctx:
            registry.validate_surfaces((transformer, bogus), self._known_ids())
        self.assertIn("no_such_surface", str(ctx.exception))
        self.assertIn("dash", str(ctx.exception))

    def test_empty_surface_list_is_rejected(self):
        transformer = registry.get_definition("transformer")
        dash = registry.get_definition("dash")
        empty = replace(dash, surfaces=())
        with self.assertRaises(ValueError) as ctx:
            registry.validate_surfaces((transformer, empty), self._known_ids())
        self.assertIn("dash", str(ctx.exception))

    def test_surface_list_omitting_classification_is_rejected(self):
        transformer = registry.get_definition("transformer")
        dash = registry.get_definition("dash")
        twins_only = replace(dash, surfaces=(registry.SURFACE_DASH_TWINS,))
        with self.assertRaises(ValueError) as ctx:
            registry.validate_surfaces((transformer, twins_only), self._known_ids())
        self.assertIn(registry.SURFACE_CLASSIFICATION, str(ctx.exception))
        self.assertIn("dash", str(ctx.exception))

    def test_production_registry_declares_only_known_surfaces(self):
        # Should not raise: this is the check the app-ready hook runs.
        registry.validate_surfaces(registry.MODELS, self._known_ids())


class SurfaceMapTests(SimpleTestCase):
    """R30/KTD11: the web layer maps a surface id to its presentation."""

    def test_every_declared_id_resolves_through_the_map(self):
        for definition in registry.MODELS:
            with self.subTest(model=definition.id):
                resolved = surfaces.resolve_surfaces(definition.surfaces)
                self.assertEqual([s.id for s in resolved], list(definition.surfaces))

    def test_pane_identity_reproduces_todays_tab_markup(self):
        classification = surfaces.get_surface(registry.SURFACE_CLASSIFICATION)
        self.assertEqual(classification.tab_id, "classification-tab")
        self.assertEqual(classification.pane_id, "classification-pane")

        twins = surfaces.get_surface(registry.SURFACE_DASH_TWINS)
        self.assertEqual(twins.tab_id, "twins-tab")
        self.assertEqual(twins.pane_id, "twins-pane")
        self.assertEqual(twins.title, "DASH Twins")

    def test_twins_surface_owns_the_twins_supporting_routes(self):
        twins = surfaces.get_surface(registry.SURFACE_DASH_TWINS)
        self.assertEqual(
            sorted(twins.routes),
            ["dash_twins", "dash_twins_data", "twins_search"],
        )

    def test_unknown_surface_id_does_not_resolve(self):
        self.assertIsNone(surfaces.get_surface("no_such_surface"))
        with self.assertRaises(ValueError) as ctx:
            surfaces.resolve_surfaces(("no_such_surface",))
        self.assertIn("no_such_surface", str(ctx.exception))


class GateConfigurationTests(SimpleTestCase):
    """R34/AE13: a gated model with unconfigured gate configuration fails closed."""

    def _gated_roster(self):
        transformer = registry.get_definition("transformer")
        dash = registry.get_definition("dash")
        gated = replace(dash, requires_credential=True, listed=False)
        return (transformer, gated)

    def _configured_env(self, **overrides):
        env = {
            gate_config.CREDENTIAL_ENV_VAR: "a-shared-credential",
            gate_config.LINK_TTL_ENV_VAR: "604800",
        }
        env.update(overrides)
        return env

    def test_missing_credential_is_rejected_naming_the_configuration(self):
        env = self._configured_env()
        del env[gate_config.CREDENTIAL_ENV_VAR]
        with patch.dict(os.environ, env, clear=True), override_settings(
            SECRET_KEY="a-real-signing-key"
        ):
            configuration = gate_config.gate_configuration()
        with self.assertRaises(ValueError) as ctx:
            registry.validate_gate_configuration(self._gated_roster(), configuration)
        self.assertIn(gate_config.CREDENTIAL_ENV_VAR, str(ctx.exception))

    def test_blank_credential_is_rejected(self):
        for blank in ("", "   ", "\t\n"):
            with self.subTest(credential=repr(blank)):
                env = self._configured_env(**{gate_config.CREDENTIAL_ENV_VAR: blank})
                with patch.dict(os.environ, env, clear=True), override_settings(
                    SECRET_KEY="a-real-signing-key"
                ):
                    configuration = gate_config.gate_configuration()
                with self.assertRaises(ValueError) as ctx:
                    registry.validate_gate_configuration(
                        self._gated_roster(), configuration
                    )
                self.assertIn(gate_config.CREDENTIAL_ENV_VAR, str(ctx.exception))

    def test_committed_default_signing_key_is_rejected(self):
        with patch.dict(
            os.environ, self._configured_env(), clear=True
        ), override_settings(SECRET_KEY="django-insecure-committed-default"):
            configuration = gate_config.gate_configuration()
        with self.assertRaises(ValueError) as ctx:
            registry.validate_gate_configuration(self._gated_roster(), configuration)
        self.assertIn(gate_config.SIGNING_KEY_NAME, str(ctx.exception))

    def test_missing_expiry_window_is_rejected(self):
        env = self._configured_env()
        del env[gate_config.LINK_TTL_ENV_VAR]
        with patch.dict(os.environ, env, clear=True), override_settings(
            SECRET_KEY="a-real-signing-key"
        ):
            configuration = gate_config.gate_configuration()
        with self.assertRaises(ValueError) as ctx:
            registry.validate_gate_configuration(self._gated_roster(), configuration)
        self.assertIn(gate_config.LINK_TTL_ENV_VAR, str(ctx.exception))

    def test_fully_configured_gate_passes(self):
        with patch.dict(
            os.environ, self._configured_env(), clear=True
        ), override_settings(SECRET_KEY="a-real-signing-key"):
            configuration = gate_config.gate_configuration()
        # Should not raise.
        registry.validate_gate_configuration(self._gated_roster(), configuration)

    def test_ungated_roster_passes_with_no_gate_configuration(self):
        with patch.dict(os.environ, {}, clear=True), override_settings(
            SECRET_KEY="django-insecure-committed-default"
        ):
            configuration = gate_config.gate_configuration()
        # Should not raise: nothing in the shipped roster is gated.
        registry.validate_gate_configuration(registry.MODELS, configuration)
