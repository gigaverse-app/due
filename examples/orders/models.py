from typing import ClassVar
from uuid import uuid7

from django.db import models

from due.django.capabilities.observed_operations import ObservedExternalOperationWork, observation_index
from due.django.evidence.effect_evidence import StoredAppliedEffectEvidence
from due.django.public import durable_work_schema


class PrintOrder(models.Model):
    reference = models.UUIDField(default=uuid7, unique=True, editable=False)
    title = models.CharField(max_length=100)
    provider_order_id = models.PositiveBigIntegerField(null=True)
    status = models.CharField(max_length=20, default="pending")


class PrintWork(ObservedExternalOperationWork):
    work_owner_field: ClassVar[str] = "owner"
    owner = models.OneToOneField(PrintOrder, on_delete=models.SET_NULL, null=True)
    reference = models.UUIDField()
    title = models.CharField(max_length=100)

    class Meta:
        indexes, constraints = durable_work_schema("print_order")
        indexes.append(observation_index("print_observation_ix"))


class PrintEvidence(StoredAppliedEffectEvidence):
    pass
