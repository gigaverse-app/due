from typing import ClassVar

from django.db import models

from due.django.public import UniverseDurableWork, durable_work_schema


class Calculation(models.Model):
    number = models.IntegerField(default=0)
    result = models.IntegerField(null=True)
    failed = models.BooleanField(default=False)


class CalculationWork(UniverseDurableWork):
    work_owner_field: ClassVar[str] = "owner"
    owner = models.OneToOneField(Calculation, on_delete=models.SET_NULL, null=True)
    number = models.IntegerField()

    class Meta:
        indexes, constraints = durable_work_schema("number_calculation")
