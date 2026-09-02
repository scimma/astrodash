"""The latent encoder + MLP (no redshift input) classifier definition."""

from astrodash.infrastructure.ml.classifiers.latent_classifier import (
    LatentNozClassifier,
)
from astrodash.infrastructure.ml.model_registry._model_definition import (
    REDSHIFT_INPUT_NONE,
    STATUS_ACTIVE,
    SURFACE_CLASSIFICATION,
    ModelDefinition,
)

LATENT_NOZ = ModelDefinition(
    id="latent_noz",
    title="DAEP Latent (no redshift)",
    description="Universal encoder plus MLP, trained without redshift as input",
    color="#d63384",
    feature_tags=("DAEP Latent", "Encoder", "No Redshift Input"),
    icon=None,
    recommended=False,
    status=STATUS_ACTIVE,
    listed=True,
    is_default=False,
    requires_credential=False,
    redshift_input=REDSHIFT_INPUT_NONE,
    preprocessing="latent",
    surfaces=(SURFACE_CLASSIFICATION,),
    supports_redshift_estimation=False,
    supports_template_overlays=False,
    supports_rlap=False,
    classifier=LatentNozClassifier,
)
