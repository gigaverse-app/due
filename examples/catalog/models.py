from typing import ClassVar
from uuid import uuid7

from django.db import models

from due.django.public import UniverseDurableWork, durable_work_schema


class CatalogEntry(models.Model):
    identity = models.UUIDField(default=uuid7, unique=True, editable=False)
    title = models.CharField(max_length=100)
    author = models.CharField(max_length=100)
    published_title = models.CharField(max_length=100, blank=True)
    indexing_failed = models.BooleanField(default=False)


class CatalogWork(UniverseDurableWork):
    work_owner_field: ClassVar[str] = "owner"
    owner = models.OneToOneField(CatalogEntry, on_delete=models.SET_NULL, null=True)
    index_key = models.CharField(max_length=64)
    title = models.CharField(max_length=100)
    author = models.CharField(max_length=100)

    class Meta:
        indexes, constraints = durable_work_schema("catalog_projection")
