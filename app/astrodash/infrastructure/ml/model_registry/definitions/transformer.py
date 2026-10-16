"""The Transformer built-in classifier definition."""

from astrodash.infrastructure.ml.classifiers.transformer_classifier import (
    TransformerClassifier,
)
from astrodash.infrastructure.ml.model_registry._model_definition import (
    REDSHIFT_INPUT_REQUIRED,
    STATUS_RETIRED,
    SURFACE_CLASSIFICATION,
    ModelDefinition,
)

TRANSFORMER = ModelDefinition(
    id="transformer",
    title="Transformer Model",
    description="Advanced transformer-based model with 5-class classification",
    color="#ff8c00",
    feature_tags=("Transformer", "5 Classes", "Fast Inference"),
    icon=None,
    recommended=False,
    status=STATUS_RETIRED,
    listed=True,
    is_default=False,
    requires_credential=False,
    redshift_input=REDSHIFT_INPUT_REQUIRED,
    preprocessing="transformer",
    surfaces=(SURFACE_CLASSIFICATION,),
    supports_redshift_estimation=False,
    supports_template_overlays=False,
    supports_rlap=False,
    classifier=TransformerClassifier,
)
