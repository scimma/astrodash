from django.db import migrations, models
import django.utils.timezone


# Rows written before this migration came from the Open Supernova Catalog path
# and carry no source spectrum identifier. Kept as a literal rather than
# imported from astrodash.models so this migration stays self-contained.
SOURCE_NAME_CATALOGUE = "osc"


def mark_pre_existing_rows_as_catalogue_sourced(apps, schema_editor):
    """Mark rows that predate source identities as catalogue-sourced.

    Their existing reference is preserved by the column rename; only the
    source marker is written, so the designation-plus-spectrum lookup can tell
    them apart from spectra fetched by identifier.

    Args:
        apps: The historical app registry.
        schema_editor: The schema editor for the connection.

    Returns:
        None.
    """
    spectrum_record = apps.get_model("astrodash", "SpectrumRecord")
    spectrum_record.objects.filter(source_name__isnull=True).update(
        source_name=SOURCE_NAME_CATALOGUE
    )


def unmark_catalogue_sourced_rows(apps, schema_editor):
    """Clear the catalogue source marker again.

    Args:
        apps: The historical app registry.
        schema_editor: The schema editor for the connection.

    Returns:
        None.
    """
    spectrum_record = apps.get_model("astrodash", "SpectrumRecord")
    spectrum_record.objects.filter(source_name=SOURCE_NAME_CATALOGUE).update(
        source_name=None
    )


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
