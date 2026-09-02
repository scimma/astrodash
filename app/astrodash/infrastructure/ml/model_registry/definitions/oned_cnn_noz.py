"""The 1D CNN (no redshift input) classifier definition."""

from astrodash.infrastructure.ml.classifiers.oned_cnn_classifier import (
    OnedCnnNozClassifier,
)
from astrodash.infrastructure.ml.model_registry._model_definition import (
    REDSHIFT_INPUT_NONE,
    STATUS_ACTIVE,
    SURFACE_CLASSIFICATION,
    ModelDefinition,
)

ONED_CNN_NOZ = ModelDefinition(
    id="1dCNN_noz",
    title="1D CNN (no redshift)",
    description="1D convolutional classifier trained on WISeREP data",
    color="#20c997",
    feature_tags=("1D CNN", "5 Classes", "No Redshift Input"),
    icon=None,
    recommended=False,
    status=STATUS_ACTIVE,
    listed=True,
    is_default=False,
    requires_credential=False,
    redshift_input=REDSHIFT_INPUT_NONE,
    preprocessing="1dcnn",
    surfaces=(SURFACE_CLASSIFICATION,),
    supports_redshift_estimation=False,
    supports_template_overlays=False,
    supports_rlap=False,
    classifier=OnedCnnNozClassifier,
)
