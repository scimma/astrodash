import yaml

from django.db import migrations


def load_fixtures(apps, schema_editor):
    """Load the initial fixtures through the models as they exist at this migration.

    ``django.core.serializers`` resolves a fixture's ``model`` key against the
    *live* app registry, so saving a deserialized object emits an INSERT naming
    every concrete column of the model as it looks today. On a fresh database
    this migration runs before any later schema change, so that INSERT names
    columns the table does not have yet and the migration fails -- which also
    breaks test-database creation, since the suite builds one from scratch.

    Reading the YAML directly and writing through ``apps.get_model`` keeps this
    migration pinned to the schema of its own point in history, so adding or
    renaming a field later cannot reach back and break it.

    Args:
        apps: Historical app registry for this migration's position.
        schema_editor: Unused; required by the ``RunPython`` signature.
    """
    for fixture_file in [
        "/app/astrodash/fixtures/initial/initial_spectrum.yaml",
        "/app/astrodash/fixtures/initial/initial_user_model.yaml",
    ]:
        print(f'''  Loading fixture "{fixture_file}"...''')
        with open(fixture_file) as fp:
            records = yaml.safe_load(fp) or []
        for record in records:
            app_label, model_name = record["model"].split(".")
            model = apps.get_model(app_label, model_name)
            known = set()
            for field in model._meta.concrete_fields:
                known.add(field.name)
                known.add(field.attname)
            fields = {
                name: value
                for name, value in (record.get("fields") or {}).items()
                if name in known
            }
            model.objects.create(pk=record["pk"], **fields)


class Migration(migrations.Migration):

    dependencies = [
        ('astrodash', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(load_fixtures),
    ]
