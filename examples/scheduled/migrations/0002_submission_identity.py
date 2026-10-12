import uuid

from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def backfill_identity(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    publications = apps.get_model("scheduled", "Publication").objects.using(schema_editor.connection.alias)
    for publication in publications.iterator(chunk_size=1000):
        publications.filter(pk=publication.pk).update(
            submission_key=uuid.uuid4(), initial_publish_at=publication.publish_at
        )


class Migration(migrations.Migration):
    dependencies = [("scheduled", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="publication",
            name="submission_key",
            field=models.UUIDField(null=True, unique=True, editable=False),
        ),
        migrations.AddField(
            model_name="publication",
            name="initial_publish_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.RunPython(backfill_identity, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="publication",
            name="submission_key",
            field=models.UUIDField(default=uuid.uuid4, unique=True, editable=False),
        ),
        migrations.AlterField(
            model_name="publication",
            name="initial_publish_at",
            field=models.DateTimeField(),
        ),
    ]
