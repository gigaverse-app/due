from django.db import migrations, models


def combine_holds_for_legacy_reader(apps, schema_editor):
    """A rollback coalesces advisory holds, preserving the most conservative retry."""
    holds = apps.get_model("due", "RecoveryDeferral").objects.using(schema_editor.connection.alias)
    duplicates = holds.values("effect", "phase", "identity").annotate(total=models.Count("pk")).filter(total__gt=1)
    for group in duplicates.iterator():
        rows = holds.filter(effect=group["effect"], phase=group["phase"], identity=group["identity"])
        retained = rows.aggregate(
            pk=models.Min("pk"),
            failures=models.Max("failures"),
            first_failed_at=models.Min("first_failed_at"),
            last_failed_at=models.Max("last_failed_at"),
            retry_at=models.Max("retry_at"),
        )
        pk = retained.pop("pk")
        rows.filter(pk=pk).update(**retained)
        rows.exclude(pk=pk).delete()


class Migration(migrations.Migration):
    dependencies = [("due", "0002_effectattempt_target_retired")]

    operations = [
        # Keep anonymous holds and old writers valid during a rolling upgrade.
        migrations.AddField(
            model_name="recoverydeferral",
            name="step",
            field=models.CharField(max_length=128, default="", db_default="", blank=True),
        ),
        migrations.RemoveConstraint(model_name="recoverydeferral", name="recovery_deferral_identity_uq"),
        migrations.RunPython(migrations.RunPython.noop, combine_holds_for_legacy_reader),
        migrations.AddConstraint(
            model_name="recoverydeferral",
            constraint=models.UniqueConstraint(
                fields=["effect", "phase", "step", "identity"], name="recovery_deferral_identity_uq"
            ),
        ),
    ]
