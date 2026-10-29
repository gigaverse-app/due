"""Bounded commit composition for already-acknowledged, self-owned work.

This holds completion callbacks, never provider calls or recovery policy. The
ordinary coordinator owns every transition, timestamp, save and finalizer.
"""

from django.db import transaction

from due.django.declarations.external_services import execution_external_services
from due.django.identities import WorkId
from due.django.integration import current_universe_scope
from due.django.models import UniverseDurableWork
from due.django.runtime.coordination import _WorkCompletion, finalize_locked_completion, require_autocommit
from due.django.runtime.metrics import DurableWorkOperation, record_work_operation
from due.django.runtime.recovery_pass import ends_the_tick
from due.django.runtime.settlement_writes import can_combine_saves, combined_receipt_writes, save_completions


class _SettlementBatch[WorkT: UniverseDurableWork]:
    def __init__(self, size: int, *, set_based: bool = False) -> None:
        self.size = size
        self.set_based = set_based
        self.pending: list[_WorkCompletion[WorkT]] = []
        self.outcomes: dict[WorkId, bool] = {}

    def add(self, completion: _WorkCompletion[WorkT]) -> bool:
        self.pending.append(completion)
        return True

    @property
    def full(self) -> bool:
        return len(self.pending) >= self.size

    def flush(self) -> None:
        require_autocommit()
        pending, self.pending = self.pending, []
        if not pending:
            return
        if len(pending) > 1:
            try:
                self.outcomes.update(self._group(pending))
                return
            except Exception as error:
                if ends_the_tick(error):
                    raise
                # The group rolled back. Preserve ordinary per-row isolation on
                # the exceptional path, without ever executing a provider again.
                pass
        errors: list[Exception] = []
        for completion in pending:
            try:
                with current_universe_scope(completion.claim.work.work_universe()), execution_external_services():
                    self.outcomes[completion.claim.work_id] = completion.complete()
            except Exception as error:
                if ends_the_tick(error):
                    raise
                errors.append(error)
        if errors:
            # Durable receipts remain eligible for the registered recovery pass.
            raise ExceptionGroup("bounded settlement failed", errors)

    def _group(self, pending: list[_WorkCompletion[WorkT]]) -> dict[WorkId, bool]:
        outcomes: dict[WorkId, bool] = {}
        query = pending[0].claim.model.objects.filter(pk__in=[item.claim.work_id for item in pending])
        with transaction.atomic():
            # Global PK order prevents two batches taking these locks in opposite
            # order. Only self-owned rows may enter this path; owner locks retain
            # their existing ordering in ordinary execution.
            current: dict[WorkId, WorkT] = {row.pk: row for row in query.order_by("pk").select_for_update()}
            owned_rows: list[tuple[_WorkCompletion[WorkT], WorkT]] = []
            for completion in pending:
                claim = completion.claim
                row = current.pop(claim.work_id, None)
                # Batch selection is only a lock acquisition. Publication still
                # needs every captured ownership fact on the freshly locked row.
                owned = row is not None and claim.owns_locked(row)
                outcomes[claim.work_id] = owned
                if not owned or row is None:
                    record_work_operation(claim.model._meta.label_lower, DurableWorkOperation.OWNERSHIP_LOST)
                    continue
                owned_rows.append((completion, row))
            if self.set_based and can_combine_saves(pending[0].claim.model):
                prepared = []
                for completion, row in owned_rows:
                    with current_universe_scope(row.work_universe()), execution_external_services():
                        prepared.append((row, completion.prepare_changes(row)))
                save_completions(prepared)
                with combined_receipt_writes():
                    for completion, row in owned_rows:
                        with current_universe_scope(row.work_universe()), execution_external_services():
                            finalize_locked_completion(row, finalize=completion.finalize)
            else:
                for completion, row in owned_rows:
                    with current_universe_scope(row.work_universe()), execution_external_services():
                        completion.settle(row)
        return outcomes
