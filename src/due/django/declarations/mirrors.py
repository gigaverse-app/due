"""Guarded product-field updates inside callbacks or declared as outcomes.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from django.db.models import Model, QuerySet
from django.utils import timezone

from due.django.models import UniverseDurableWork
from due.django.runtime.coordination import require_fenced_settlement


@dataclass(frozen=True)
class Outcome[WorkT: UniverseDurableWork, ValueT]:
    """A fenced ORM update; its dynamic field mapping is validated by Django."""

    _update: Callable[[WorkT, ValueT], None]

    def __call__(self, work: WorkT, value: ValueT) -> None:
        require_fenced_settlement(work)
        self._update(work, value)


@dataclass(frozen=True)
class StatusMirror[WorkT: UniverseDurableWork, OwnerT: Model]:
    """One canonical target/guard for an application's product outcome table."""

    target: Callable[[WorkT], QuerySet[OwnerT]]
    touch: str | None = None

    def outcome[ValueT](
        self,
        values: Mapping[str, object] | Callable[[WorkT, ValueT], Mapping[str, object]],
        *,
        where: Callable[[QuerySet[OwnerT]], QuerySet[OwnerT]] | None = None,
        no_match: Callable[[], Exception] | None = None,
    ) -> Outcome[WorkT, ValueT]:
        """Common target first, outcome filter second; skipped outcomes still run their hook."""
        # Copy the field mapping; individual ORM values remain application-owned.
        declared = values if callable(values) else dict(values)

        def update(work: WorkT, value: ValueT) -> None:
            fields = dict(declared(work, value) if callable(declared) else declared)
            self._update_fields(work, fields, where=where, no_match=no_match)

        return Outcome(update)

    def update(
        self,
        work: WorkT,
        *,
        where: Callable[[QuerySet[OwnerT]], QuerySet[OwnerT]] | None = None,
        no_match: Callable[[], Exception] | None = None,
        **fields: object,
    ) -> None:
        """Update guarded product rows directly inside an ordinary callback."""
        self._update_fields(work, fields, where=where, no_match=no_match)

    def _update_fields(
        self,
        work: WorkT,
        fields: dict[str, object],
        *,
        where: Callable[[QuerySet[OwnerT]], QuerySet[OwnerT]] | None,
        no_match: Callable[[], Exception] | None,
    ) -> None:
        # Keep mappings separate from helper keywords: the rich API can update
        # product fields named "where" or "no_match" too.
        require_fenced_settlement(work)
        if self.touch is not None:
            fields[self.touch] = timezone.now()
        rows = self.target(work)
        if where is not None:
            rows = where(rows)
        matched = rows.update(**fields)
        if not matched and no_match is not None:
            raise no_match()
