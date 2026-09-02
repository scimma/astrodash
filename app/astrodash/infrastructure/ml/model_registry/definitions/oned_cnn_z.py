"""The 1D CNN (redshift-required) classifier definition."""

from astrodash.infrastructure.ml.classifiers.oned_cnn_classifier import (
    OnedCnnZClassifier,
)
from astrodash.infrastructure.ml.model_registry._model_definition import (
    REDSHIFT_INPUT_REQUIRED,
    STATUS_ACTIVE,
    SURFACE_CLASSIFICATION,
    ModelDefinition,
)

ONED_CNN_Z = ModelDefinition(
    id="1dCNN_z",
    title="1D CNN (redshift)",
    description="1D convolutional classifier trained on WISeREP data",
    color="#0d6efd",
    feature_tags=("1D CNN", "5 Classes", "Redshift Input"),
    icon=None,
    recommended=False,
    status=STATUS_ACTIVE,
    listed=True,
    is_default=False,
    requires_credential=False,
    redshift_input=REDSHIFT_INPUT_REQUIRED,
    preprocessing="1dcnn",
    surfaces=(SURFACE_CLASSIFICATION,),
    supports_redshift_estimation=False,
    supports_template_overlays=False,
    supports_rlap=False,
    classifier=OnedCnnZClassifier,
)
