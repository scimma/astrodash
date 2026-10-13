import logging

from django.db import migrations, models
import django.utils.timezone


logger = logging.getLogger(__name__)


# Rows written before this migration by the Open Supernova Catalog path carry a
# designation (the column renamed here from ``osc_ref``) and no source spectrum
# identifier. Kept as a literal rather than imported from astrodash.models so
# this migration stays self-contained.
SOURCE_NAME_CATALOGUE = "osc"


def mark_pre_existing_rows_as_catalogue_sourced(apps, schema_editor):
    """Mark catalogue-sourced rows that predate source identities.

    A designation is the only evidence that a pre-existing row came from the
    Open Supernova Catalog: the catalogue path was the sole writer of
    ``osc_ref``. Rows without one -- the initial fixtures and every uploaded
    spectrum -- were never fetched from the catalogue, so they keep a null
    ``source_name`` rather than being mislabelled. The designation itself is
    preserved by the column rename; only the source marker is written, so the
    designation-plus-spectrum lookup can tell these rows apart from spectra
    fetched by identifier.

    Args:
        apps: The historical app registry.
        schema_editor: The schema editor for the connection.

    Returns:
        None.
    """
    spectrum_record = apps.get_model("astrodash", "SpectrumRecord")
    marked = spectrum_record.objects.filter(
        source_name__isnull=True, object_designation__isnull=False
    ).update(source_name=SOURCE_NAME_CATALOGUE)
    logger.info("Marked %d pre-existing spectra as catalogue-sourced", marked)


def unmark_catalogue_sourced_rows(apps, schema_editor):
    """Clear the catalogue source marker from the rows the backfill marked.

    Mirrors the forward predicate so the reverse touches only rows the forward
    pass could have written.

    Args:
        apps: The historical app registry.
        schema_editor: The schema editor for the connection.

    Returns:
        None.
    """
    spectrum_record = apps.get_model("astrodash", "SpectrumRecord")
    cleared = spectrum_record.objects.filter(
        source_name=SOURCE_NAME_CATALOGUE, object_designation__isnull=False
    ).update(source_name=None)
    logger.info("Cleared the catalogue source marker from %d spectra", cleared)


class Migration(migrations.Migration):
    dependencies = [
        ("astrodash", "0002_load_fixtures"),
    ]

    operations = [
        migrations.RenameField(
            model_name="spectrumrecord",
            old_name="osc_ref",
            new_name="object_designation",
        ),
        migrations.AddField(
            model_name="spectrumrecord",
            name="source_name",
            field=models.CharField(blank=True, null=True, max_length=32),
        ),
        migrations.AddField(
            model_name="spectrumrecord",
            name="source_spectrum_id",
            field=models.CharField(blank=True, null=True, max_length=128),
        ),
        migrations.AddIndex(
            model_name="spectrumrecord",
            index=models.Index(
                fields=["source_name", "object_designation", "source_spectrum_id"],
                name="astrodash_spec_source_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="spectrumrecord",
            constraint=models.UniqueConstraint(
                fields=["object_designation", "source_spectrum_id"],
                name="astrodash_spec_designation_spectrum_uniq",
            ),
        ),
        migrations.RunPython(
            mark_pre_existing_rows_as_catalogue_sourced,
            unmark_catalogue_sourced_rows,
        ),
        migrations.CreateModel(
            name="SpectrumCandidateSetRecord",
            fields=[
                (
                    "normalized_name",
                    models.CharField(primary_key=True, max_length=128, serialize=False),
                ),
                ("candidates", models.JSONField()),
                (
                    "resolved_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
            ],
            options={
                "db_table": "astrodash_spectrum_candidate_sets",
                "ordering": ["-resolved_at"],
            },
        ),
    ]
