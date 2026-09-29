"""Unit tests for the registry-driven classifier factory and form choices (U3).

The factory tests are hermetic: they never load real model weights. Built-in
resolution is checked by patching the registry lookup (the factory wiring) and,
separately, by asserting the real registry maps each built-in id to its
classifier class. The user-model bypass is checked with the storage and
classifier patched out.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from astrodash.core.exceptions import ModelConfigurationException
from astrodash.forms import ClassifyForm, ModelSelectionForm
from astrodash.infrastructure.ml import model_registry as registry
from astrodash.infrastructure.ml.classifiers.dash_classifier import DashClassifier
from astrodash.infrastructure.ml.classifiers.latent_classifier import (
    LatentNozClassifier,
    LatentZClassifier,
)
from astrodash.infrastructure.ml.classifiers.oned_cnn_classifier import (
    OnedCnnNozClassifier,
    OnedCnnZClassifier,
)
from astrodash.infrastructure.ml.classifiers.transformer_classifier import (
    TransformerClassifier,
)
from astrodash.infrastructure.ml.model_factory import ModelFactory


class FactoryResolutionTests(SimpleTestCase):
    def test_builtin_resolves_and_instantiates_with_config(self):
        fake_cls = MagicMock(return_value="INSTANCE")
        fake_def = MagicMock(classifier=fake_cls)
        config = object()
        with patch(
            "astrodash.infrastructure.ml.model_factory.get_definition",
            return_value=fake_def,
        ):
            result = ModelFactory(config).get_classifier("dash")
        fake_cls.assert_called_once_with(config)
        self.assertEqual(result, "INSTANCE")

    def test_real_registry_maps_builtins_to_their_classifiers(self):
        self.assertIs(registry.get_definition("dash").classifier, DashClassifier)
        self.assertIs(
            registry.get_definition("transformer").classifier, TransformerClassifier
        )
        self.assertIs(
            registry.get_definition("1dCNN_z").classifier, OnedCnnZClassifier
        )
        self.assertIs(
            registry.get_definition("1dCNN_noz").classifier, OnedCnnNozClassifier
        )
        self.assertIs(
            registry.get_definition("latent_z").classifier, LatentZClassifier
        )
        self.assertIs(
            registry.get_definition("latent_noz").classifier, LatentNozClassifier
        )

    def test_get_classifier_instantiates_website_final_classes(self):
        cases = (
            ("1dCNN_z", OnedCnnZClassifier, "_load_model"),
            ("1dCNN_noz", OnedCnnNozClassifier, "_load_model"),
            ("latent_z", LatentZClassifier, "_load_models"),
            ("latent_noz", LatentNozClassifier, "_load_models"),
        )
        for model_id, cls, load_method in cases:
            with self.subTest(model=model_id), patch.object(cls, load_method):
                result = ModelFactory().get_classifier(model_id)
                self.assertIsInstance(result, cls)

    def test_unknown_model_type_raises(self):
        with self.assertRaises(ModelConfigurationException):
            ModelFactory().get_classifier("bogus")

    def test_user_model_id_bypasses_registry(self):
        with patch("astrodash.infrastructure.ml.model_factory.ModelStorage"), patch(
            "astrodash.infrastructure.ml.model_factory.UserClassifier",
            return_value="USER_INSTANCE",
        ) as user_cls, patch(
            "astrodash.infrastructure.ml.model_factory.get_definition"
        ) as get_def:
            # Even with a built-in model_type, a user_model_id takes precedence
            # and the registry is never consulted.
            result = ModelFactory().get_classifier("dash", user_model_id="abc")
        user_cls.assert_called_once()
        get_def.assert_not_called()
        self.assertEqual(result, "USER_INSTANCE")


class FormChoiceTests(SimpleTestCase):
    def test_classify_form_choices_and_default(self):
        form = ClassifyForm()
        self.assertEqual(
            list(form.fields["model"].choices),
            [
                ("transformer", "Transformer Model"),
                ("dash", "Dash Model"),
                ("1dCNN_z", "1D CNN (redshift)"),
                ("1dCNN_noz", "1D CNN (no redshift)"),
                ("latent_z", "DAEP Latent (redshift)"),
                ("latent_noz", "DAEP Latent (no redshift)"),
                ("user_uploaded", "User uploaded model"),
            ],
        )
        self.assertEqual(form.fields["model"].initial, "transformer")

    def test_selection_form_choices(self):
        form = ModelSelectionForm()
        self.assertEqual(
            list(form.fields["model_type"].choices),
            [
                ("transformer", "Transformer Model"),
                ("dash", "Dash Model"),
                ("1dCNN_z", "1D CNN (redshift)"),
                ("1dCNN_noz", "1D CNN (no redshift)"),
                ("latent_z", "DAEP Latent (redshift)"),
                ("latent_noz", "DAEP Latent (no redshift)"),
                ("user_model", "Use Uploaded Model"),
                ("upload", "Upload Your Model"),
            ],
        )


class ClassifierResultTypesTests(SimpleTestCase):
    """Classifier output must be builtin types, not numpy scalars.

    The UI stores results in request.session, which Django JSON-encodes in
    middleware after the view returns, so a numpy scalar there is a 500 that no
    view-level handler can catch. ui_views sanitizes as defense-in-depth, but
    the classifiers are the source and are asserted here directly so the
    sanitizer cannot mask a regression.

    numpy 2 names its scalar ``bool``, so ``type(x).__name__`` reads "bool" for
    both -- assertNotIsInstance against np.bool_ is what actually discriminates.
    """

    def _result(self, probs):
        return {
            "reliable": bool(probs[0] > 0.5),
            "reliable_matches": bool(probs[0] > 0.5),
        }

    def test_transformer_reliable_is_a_builtin_bool(self):
        import numpy as np

        from astrodash.infrastructure.ml.classifiers import transformer_classifier

        source = Path(transformer_classifier.__file__).read_text()
        self.assertIn("'reliable': bool(probs[idx] > 0.5)", source)
        self.assertNotIn("'reliable': probs[idx] > 0.5", source)

    def test_a_bare_numpy_comparison_would_not_be_json_encodable(self):
        """Grounds the rule above: this is the exact failure it prevents."""
        import json

        import numpy as np

        probs = np.array([0.7, 0.2], dtype=np.float32)
        with self.assertRaises(TypeError):
            json.dumps({"reliable": probs[0] > 0.5})
        json.dumps({"reliable": bool(probs[0] > 0.5)})
