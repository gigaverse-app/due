from typing import ClassVar

from django.db import models

from due.django.public import UniverseDurableWork, durable_work_schema


class Book(models.Model):
    """An immutable submission; publication makes its external index entry visible."""

    title = models.CharField(max_length=100)
    author = models.CharField(max_length=100)
    index_key = models.CharField(max_length=64, blank=True)
    indexing_failed = models.BooleanField(default=False)


class BookWork(UniverseDurableWork):
    work_owner_field: ClassVar[str] = "owner"
    owner = models.OneToOneField(Book, on_delete=models.SET_NULL, null=True)

    class Meta:
        abstract = True


class IndexBookWork(BookWork):
    title = models.CharField(max_length=100)
    author = models.CharField(max_length=100)

    class Meta:
        indexes, constraints = durable_work_schema("book_index")


class PublishBookWork(BookWork):
    index_key = models.CharField(max_length=64)

    class Meta:
        indexes, constraints = durable_work_schema("book_publication")
