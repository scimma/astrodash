from django.db import models
from django.utils import timezone
import uuid


#: Source marker written on rows that predate the WISeREP by-name path.
SOURCE_NAME_CATALOGUE = "osc"

#: Source marker written on spectra fetched from WISeREP.
SOURCE_NAME_WISEREP = "wiserep"


class SpectrumRecord(models.Model):
    """Persisted snapshot of a processed spectrum.

    A row is identified upstream by ``object_designation`` together with
    ``source_spectrum_id``: one object may have several retained epochs, each
    carrying the source's own identifier for that spectrum. ``created_at``
    doubles as the time the spectrum was retrieved from its source.
    """

    id = models.CharField(primary_key=True, max_length=64, default=lambda: str(uuid.uuid4()), editable=False)
    object_designation = models.CharField(
        max_length=128, blank=True, null=True, db_index=True
    )
    source_name = models.CharField(max_length=32, blank=True, null=True)
    source_spectrum_id = models.CharField(max_length=128, blank=True, null=True)
    file_name = models.CharField(max_length=255, blank=True, null=True)
    x = models.JSONField()
    y = models.JSONField()
    redshift = models.FloatField(blank=True, null=True)
    meta = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "astrodash_spectra"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["source_name", "object_designation", "source_spectrum_id"],
                name="astrodash_spec_source_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["object_designation", "source_spectrum_id"],
                name="astrodash_spec_designation_spectrum_uniq",
            ),
        ]


class UserModelRecord(models.Model):
    """Metadata for a user-uploaded ML model stored on disk."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    owner = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    model_path = models.CharField(max_length=512)
    class_mapping_path = models.CharField(max_length=512)
    input_shape_path = models.CharField(max_length=512)
    meta = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "astrodash_user_models"
        ordering = ["-created_at"]


class SpectrumCandidateSetRecord(models.Model):
    """Ranked candidate set resolved for one submitted object name.

    The set is keyed by the normalized submitted name so that a repeat lookup
    can reach an already retained spectrum by its source spectrum identifier
    without resolving the name again. Each entry in ``candidates`` is a mapping
    carrying ``source_spectrum_id``, ``observation_date``, ``instrument``,
    ``quality``, ``redshift`` and ``download_url``.
    """

    normalized_name = models.CharField(primary_key=True, max_length=128)
    candidates = models.JSONField()
    resolved_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "astrodash_spectrum_candidate_sets"
        ordering = ["-resolved_at"]
