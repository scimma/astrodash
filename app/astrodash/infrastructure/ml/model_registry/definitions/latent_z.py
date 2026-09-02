"""The latent encoder + MLP (redshift-required) classifier definition."""

from astrodash.infrastructure.ml.classifiers.latent_classifier import LatentZClassifier
from astrodash.infrastructure.ml.model_registry._model_definition import (
    REDSHIFT_INPUT_REQUIRED,
    STATUS_ACTIVE,
    SURFACE_CLASSIFICATION,
    ModelDefinition,
)

LATENT_Z = ModelDefinition(
    id="latent_z",
    title="DAEP Latent (redshift)",
    description="Universal encoder plus MLP, trained with deredshifted spectra",
    color="#6f42c1",
    feature_tags=("DAEP Latent", "Encoder", "Redshift Input"),
    icon=None,
    recommended=False,
    status=STATUS_ACTIVE,
    listed=True,
    is_default=False,
    requires_credential=False,
    redshift_input=REDSHIFT_INPUT_REQUIRED,
    preprocessing="latent",
    surfaces=(SURFACE_CLASSIFICATION,),
    supports_redshift_estimation=False,
    supports_template_overlays=False,
    supports_rlap=False,
    classifier=LatentZClassifier,
)
