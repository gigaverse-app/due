"""
Fenced completion on a model that already owns its lifecycle.

Configure native nullable timestamps and a product validator. claim() issues
a token; complete(finalize=...) commits named database-only product writes and
the receipt together; release() cannot clear a successor. held(retain_on_failure=
True) makes a failed attempt wait out its lease exactly like a crashed one, and
available() lets a sweep skip rows claim() would refuse. Provider work belongs
in ordinary application methods before settlement and must survive replay.
Pair with recovery.py when the same canonical row also needs delivery exclusion.

Design and usage: docs/native-lifecycles.md.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import models, transaction
from django.db.models import Q, QuerySet

from due.django.runtime.coordination import WorkOwnershipLost, database_now


@dataclass(frozen=True, kw_only=True)
class CompletionLease[RowT: models.Model]:
    """Reuse native timestamp columns; product validation runs on the locked row."""

    model: type[RowT]
    claimed_at: str
    completed_at: str
    validate: Callable[[RowT], None]
    updated_at: str | None = None

    def __post_init__(self) -> None:
        if self.claimed_at == self.completed_at:
            raise ValueError("claim and completion columns must be distinct")
        for name in (self.claimed_at, self.completed_at):
            field = self.model._meta.get_field(name)
            if not isinstance(field, models.DateTimeField) or not field.null:
                raise ValueError(f"{name} must be a nullable DateTimeField")
        if self.updated_at is not None:
            if self.updated_at in (self.claimed_at, self.completed_at):
                raise ValueError("update, claim and completion columns must be distinct")
            if not isinstance(self.model._meta.get_field(self.updated_at), models.DateTimeField):
                raise ValueError("the update column must be a DateTimeField")

    def claim(self, row_id: int, *, lease_duration: timedelta = timedelta(minutes=1)) -> datetime | None:
        if lease_duration <= timedelta(0):
            raise ValueError("lease duration must be positive")
        with transaction.atomic():
            row = self.model.objects.select_for_update().get(pk=row_id)
            # Field names are the explicit dynamic ORM boundary, validated above.
            claimed = getattr(row, self.claimed_at)
            completed = getattr(row, self.completed_at)
            assert claimed is None or isinstance(claimed, datetime)
            assert completed is None or isinstance(completed, datetime)
            now = database_now()
            if completed is not None or (claimed is not None and claimed > now - lease_duration):
                return None
            self.validate(row)
            updates = {self.claimed_at: now}
            if self.updated_at is not None:
                updates[self.updated_at] = now
            self.model.objects.filter(pk=row_id).update(**updates)
            return now

    @contextmanager
    def held(
        self,
        row_id: int,
        *,
        lease_duration: timedelta = timedelta(minutes=1),
        retain_on_failure: bool = False,
    ) -> Iterator[datetime | None]:
        """
        Release this claim on scope exit; never clear a successor or a completed receipt.

        With retain_on_failure, only a normal exit releases. An exception, a
        cancellation or worker death keeps the original claim until its original
        expiry, so a failed attempt is retried no sooner than a crashed one: an
        interrupted provider call is outcome-unknown either way. Nothing is
        caught; every exception propagates.
        """
        token = self.claim(row_id, lease_duration=lease_duration)
        succeeded = False
        try:
            yield token
            succeeded = True
        finally:
            if token is not None and (succeeded or not retain_on_failure):
                self.release(row_id, token=token)

    def available(self, owed: QuerySet[RowT], *, lease_duration: timedelta) -> QuerySet[RowT]:
        """
        Narrow owed rows to those claim() would admit: no receipt and no live claim.

        Sweeps apply this before dispatch so a retained or running claim is not
        redelivered as a no-op. claim() still decides under the row lock and runs
        product validation; this filter only avoids pointless deliveries.
        """
        if owed.model is not self.model or owed.query.is_sliced:
            raise ValueError("completion availability requires an unsliced query for its canonical model")
        if lease_duration <= timedelta(0):
            raise ValueError("lease duration must be positive")
        return owed.filter(**{f"{self.completed_at}__isnull": True}).filter(
            Q(**{f"{self.claimed_at}__isnull": True})
            | Q(**{f"{self.claimed_at}__lte": database_now() - lease_duration})
        )

    def _owned(self, row_id: int, token: datetime) -> QuerySet[RowT]:
        # The captured token is part of every write: a resumed worker cannot
        # complete or release a successor's claim, even on the same product row.
        return self.model.objects.filter(pk=row_id, **{self.claimed_at: token, f"{self.completed_at}__isnull": True})

    def complete(self, row_id: int, *, token: datetime, finalize: Callable[[RowT], None] | None = None) -> bool:
        """
        Commit named database-only product writes and the receipt under one lock.

        Provider I/O belongs before this call and must be safe to repeat after
        a local rollback. False means this token no longer owns completion;
        the finalizer was not entered. Any finalizer/receipt failure rolls back
        all writes. The finalizer must leave the ownership columns alone.
        """
        with transaction.atomic():
            row = self._owned(row_id, token).select_for_update().first()
            if row is None:
                return False
            self.validate(row)
            if finalize is not None:
                finalize(row)
            now = database_now()
            updates: dict[str, datetime | None] = {self.claimed_at: None, self.completed_at: now}
            if self.updated_at is not None:
                updates[self.updated_at] = now
            if not self._owned(row_id, token).update(**updates):
                raise WorkOwnershipLost("finalization changed the completion authority")
            return True

    def release(self, row_id: int, *, token: datetime) -> bool:
        updates: dict[str, datetime | None] = {self.claimed_at: None}
        if self.updated_at is not None:
            updates[self.updated_at] = database_now()
        return bool(self._owned(row_id, token).update(**updates))
