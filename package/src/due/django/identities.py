"""Typed work identity and its indexed attempt correlation column."""

from typing import Literal
from uuid import UUID

from django.db import models

type WorkId = int | UUID


def attempt_key_field(model: type[models.Model]) -> Literal["work_id", "work_uuid"]:
    """Select a typed column; never cast an indexed key to text."""
    field = model._meta.pk
    while isinstance(field, models.ForeignKey):
        field = field.target_field
    if isinstance(field, models.UUIDField):
        return "work_uuid"
    if isinstance(field, models.IntegerField):
        return "work_id"
    raise TypeError(f"{model.__name__} work primary key must be integer or UUID")


def work_id_to_wire(identity: WorkId) -> int | str:
    """Keep existing integer messages unchanged; UUID messages use canonical text."""
    return str(identity) if isinstance(identity, UUID) else identity


def work_id_from_wire(model: type[models.Model], identity: int | str | UUID) -> WorkId:
    """Normalize transport identity through the selected model's native field."""
    attempt_key_field(model)
    field = model._meta.pk
    while isinstance(field, models.ForeignKey):
        field = field.target_field
    value = field.to_python(identity)
    if not isinstance(value, (int, UUID)):
        raise ValueError("work identity must decode to integer or UUID")
    return value
