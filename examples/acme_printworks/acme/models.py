from typing import ClassVar
from uuid import uuid4

from django.db import models
from due.django.capabilities.observed_operations import (
    ObservedExternalOperationWork,
    observation_index,
)
from due.django.evidence.effect_evidence import StoredAppliedEffectEvidence
from due.django.public import UniverseDurableWork, durable_work_schema


class Job(models.Model):
    customer = models.CharField(max_length=100, default="Willow Cafe")
    published_key = models.CharField(max_length=200, blank=True)
    proof_failed = models.BooleanField(default=False)
    portal_failed = models.BooleanField(default=False)
    current_revision = models.ForeignKey("Revision", null=True, on_delete=models.PROTECT, related_name="+")


class Revision(models.Model):
    job = models.ForeignKey(Job, on_delete=models.PROTECT)
    number = models.PositiveIntegerField()
    text = models.TextField()
    word_count = models.PositiveIntegerField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["job", "number"], name="acme_revision_number")]


class ProofWork(UniverseDurableWork):
    work_owner_field: ClassVar[str] = "owner"
    owner = models.OneToOneField(Job, null=True, on_delete=models.SET_NULL)
    revision_id = models.PositiveBigIntegerField()
    text = models.TextField()

    class Meta:
        indexes, constraints = durable_work_schema("acme_proof")


class PortalWork(UniverseDurableWork):
    work_owner_field: ClassVar[str] = "owner"
    owner = models.OneToOneField(Job, null=True, on_delete=models.SET_NULL)
    proof_key = models.CharField(max_length=200)

    class Meta:
        indexes, constraints = durable_work_schema("acme_portal")


class PrintOrder(models.Model):
    reference = models.UUIDField(default=uuid4, unique=True, editable=False)
    revision = models.ForeignKey(Revision, on_delete=models.PROTECT)
    proof_key = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField(default=500)
    status = models.CharField(max_length=20, default="pending")
    provider_order_id = models.PositiveBigIntegerField(null=True)


class PrintWork(ObservedExternalOperationWork):
    work_owner_field: ClassVar[str] = "owner"
    owner = models.OneToOneField(PrintOrder, null=True, on_delete=models.SET_NULL)
    reference = models.UUIDField()
    proof_key = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField()

    class Meta:
        indexes, constraints = durable_work_schema("acme_print")
        indexes.append(observation_index("acme_print_observe"))


class PrintEvidence(StoredAppliedEffectEvidence):
    pass


class CleanupWork(UniverseDurableWork):
    spool_key = models.CharField(max_length=200)

    class Meta:
        indexes, constraints = durable_work_schema("acme_cleanup")
