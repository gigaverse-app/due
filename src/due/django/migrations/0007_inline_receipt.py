from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError


def require_empty_inline_receipts(apps, schema_editor):
    attempt = apps.get_model("due", "EffectAttempt")
    # Hold this through column removal: no writer can add evidence between
    # the guard and the destructive DDL in this atomic migration.
    schema_editor.execute(f"LOCK TABLE {schema_editor.quote_name(attempt._meta.db_table)} IN ACCESS EXCLUSIVE MODE")
    if attempt.objects.using(schema_editor.connection.alias).filter(receipt__isnull=False).exists():
        raise IrreversibleError("migrate retained inline receipts back before removing inline receipt storage")


class Migration(migrations.Migration):
    dependencies = [("due", "0006_merge_20261027_0156")]

    operations = [
        migrations.AddField(model_name="effectattempt", name="receipt", field=models.JSONField(null=True)),
        migrations.AddField(
            model_name="effectattempt",
            name="receipt_pending",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.RunPython(migrations.RunPython.noop, require_empty_inline_receipts),
    ]
