"""Typed work identity and its indexed attempt correlation column."""

from typing import Literal
from uuid import UUID

from django.db import models

type WorkId = int | UUID


def _work_pk(model: type[models.Model]) -> models.IntegerField | models.UUIDField:
    """Resolve and validate the native key once, including relational primary keys."""
    field = model._meta.pk
    while isinstance(field, models.ForeignKey):
        field = field.target_field
    if not isinstance(field, (models.IntegerField, models.UUIDField)):
        raise TypeError(f"{model.__name__} work primary key must be integer or UUID")
    return field


def attempt_key_field(model: type[models.Model]) -> Literal["work_id", "work_uuid"]:
    """Select a typed column; never cast an indexed key to text."""
    return "work_uuid" if isinstance(_work_pk(model), models.UUIDField) else "work_id"


def work_id_to_wire(identity: WorkId) -> int | str:
    """Keep existing integer messages unchanged; UUID messages use canonical text."""
    return str(identity) if isinstance(identity, UUID) else identity


def work_id_from_wire(model: type[models.Model], identity: int | str | UUID) -> WorkId:
    """Normalize transport identity through the selected model's native field."""
    value = _work_pk(model).to_python(identity)
    if not isinstance(value, (int, UUID)):
        raise ValueError("work identity must decode to integer or UUID")
    return value
