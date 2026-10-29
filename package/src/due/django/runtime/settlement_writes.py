"""Set-based persistence for explicitly independent, already-fenced completions.

The ordinary coordinator prepares and finalizes each transition. This module
only combines writes inside that coordinator's existing transaction.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

from django.db import connection, models
from django.db.models.signals import post_save, pre_save

from due.django.models import DurableWork, EffectAttempt

_SAVE = models.Model.save
_SAVE_BASE = models.Model.save_base
_SAVE_TABLE = cast(Any, models.Model)._save_table  # Django's implementation is absent from its stubs.
_DO_UPDATE = models.Model._do_update


def can_combine_saves(model: type[DurableWork]) -> bool:
    """Custom model persistence and signals always retain ordinary model saves."""
    return (
        type(model.objects) is models.Manager
        and not model._meta.parents
        and model.save is _SAVE
        and model.save_base is _SAVE_BASE
        and cast(Any, model)._save_table is _SAVE_TABLE
        and model._do_update is _DO_UPDATE
        and not pre_save.has_listeners(model)
        and not post_save.has_listeners(model)
        and all(
            type(column).__module__.startswith("django.db.models.") and not column.generated
            for column in model._meta.concrete_fields
        )
    )


def save_completions(rows: Sequence[tuple[DurableWork, Sequence[str]]]) -> None:
    """Persist prepared rows with typed VALUES, retaining Django field preparation."""
    if not connection.in_atomic_block:
        raise RuntimeError("combined completion writes require the work-lock transaction")
    groups: dict[tuple[str, ...], list[DurableWork]] = {}
    for row, names in rows:
        groups.setdefault(tuple(dict.fromkeys(names)), []).append(row)
    quote = connection.ops.quote_name
    for names, work in groups.items():
        model = type(work[0])
        fields = [cast(models.Field, model._meta.get_field(name)) for name in names]
        pk = model._meta.pk
        assert pk is not None
        compiler = model.objects.all().query.get_compiler(connection=connection)
        values: list[str] = []
        params: list[Any] = []
        for row in work:
            cells: list[str] = []
            for column in [pk, *fields]:
                assert isinstance(column, models.Field)
                value = row.pk if column is pk else column.pre_save(row, add=False)
                value = column.get_db_prep_save(value, connection)
                if hasattr(value, "resolve_expression"):
                    expression = value.resolve_expression(compiler.query, allow_joins=False, for_save=True)
                    sql, expression_params = compiler.compile(expression)
                    cells.append(sql)
                    params.extend(expression_params)
                else:
                    cells.append(f"%s::{column.db_type(connection)}")
                    params.append(value)
            values.append("(" + ", ".join(cells) + ")")
        columns = [quote(cast(str, column.column)) for column in [pk, *fields]]
        table = quote(model._meta.db_table)
        assignments = ", ".join(f"{column} = incoming.{column}" for column in columns[1:])
        completed_column = quote(cast(str, cast(models.Field, model._meta.get_field("completed_at")).column))
        updated_column = quote(cast(str, cast(models.Field, model._meta.get_field("updated_at")).column))
        sql = (
            f"UPDATE {table} AS target SET {assignments} FROM (VALUES {', '.join(values)}) "
            f"AS incoming ({', '.join(columns)}) WHERE target.{columns[0]} = incoming.{columns[0]} "
            f"RETURNING target.{columns[0]}, target.{completed_column}, target.{updated_column}"
        )
        # All identifiers/types come from Django model metadata; values remain
        # bound parameters. These rows were freshly locked and fenced by the engine.
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            persisted = {identity: (completed, updated) for identity, completed, updated in cursor.fetchall()}
        if len(persisted) != len(work):
            raise RuntimeError("a prepared completion disappeared while its work lock was held")
        for row in work:
            row.completed_at, row.updated_at = persisted[row.pk]


@dataclass
class _ReceiptWrites:
    transaction: object
    savepoints: tuple[object, ...]
    pending: dict[type[models.Model] | None, set[UUID]] = field(default_factory=dict)
    active: bool = True


_receipt_writes: ContextVar[_ReceiptWrites | None] = ContextVar("due_settlement_receipt_writes", default=None)


def finish_receipt(attempt: EffectAttempt, receipt_model: type[models.Model] | None) -> None:
    scope = _receipt_writes.get()
    if (
        scope is not None
        and scope.active
        and connection.in_atomic_block
        and connection.atomic_blocks[-1] is scope.transaction
        and tuple(connection.savepoint_ids) == scope.savepoints
    ):
        scope.pending.setdefault(receipt_model, set()).add(attempt.pk)
        return
    _finish_receipts([attempt.pk], receipt_model)


def _finish_receipts(identities: Sequence[UUID], receipt_model: type[models.Model] | None) -> None:
    updates = {"repair_pending": False}
    if receipt_model is None:
        updates["receipt_pending"] = False
    EffectAttempt.objects.filter(pk__in=identities).update(**updates)
    if receipt_model is not None:
        receipt_model.objects.filter(attempt_id__in=identities).update(settlement_pending=False)


@contextmanager
def combined_receipt_writes() -> Iterator[None]:
    if not connection.in_atomic_block:
        raise RuntimeError("combined receipt writes require the settlement transaction")
    scope = _ReceiptWrites(connection.atomic_blocks[-1], tuple(connection.savepoint_ids))
    token = _receipt_writes.set(scope)
    try:
        yield
        for model, identities in scope.pending.items():
            _finish_receipts(sorted(identities), model)
    finally:
        scope.active = False
        _receipt_writes.reset(token)
