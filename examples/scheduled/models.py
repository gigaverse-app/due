from typing import ClassVar
from uuid import uuid7

from django.db import models

from due.django.capabilities.scheduled import ScheduledWork
from due.django.models import durable_work_schema


class Publication(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled"
        PUBLISHED = "published"
        CANCELLED = "cancelled"
        FAILED = "failed"

    submission_key = models.UUIDField(default=uuid7, unique=True, editable=False)
    initial_publish_at = models.DateTimeField()
    text = models.TextField()
    publish_at = models.DateTimeField()
    status = models.CharField(max_length=12, choices=Status, default=Status.SCHEDULED)
    published_key = models.CharField(max_length=64, blank=True)


class PublicationDeadline(ScheduledWork):
    work_owner_field: ClassVar[str] = "owner"
    owner = models.OneToOneField(Publication, on_delete=models.SET_NULL, null=True)
    text = models.TextField()

    class Meta:
        indexes, constraints = durable_work_schema("publication_deadline")
