from django.contrib.postgres.operations import AddConstraintNotValid, AddIndexConcurrently, ValidateConstraint
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("due", "0007_inline_receipt")]

    operations = [
        AddConstraintNotValid(
            model_name="effectattempt",
            constraint=models.CheckConstraint(
                condition=models.Q(receipt_pending=False) | models.Q(receipt__isnull=False),
                name="effect_attempt_receipt_ck",
            ),
        ),
        ValidateConstraint(model_name="effectattempt", name="effect_attempt_receipt_ck"),
        AddIndexConcurrently(
            model_name="effectattempt",
            index=models.Index(
                fields=["work_model", "id"],
                condition=models.Q(receipt_pending=True),
                name="effect_attempt_receipt_ix",
            ),
        ),
    ]
