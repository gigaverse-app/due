"""
Real evidence histories distinguish receipts from outstanding repair obligations.

Design: docs/architecture.md.
Successful retries cannot confirm earlier timed-out requests finished. Both
scheduled actions and projections retain that uncertainty until an exact receipt.
"""

from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django.db import transaction
from django.utils import timezone

from due.django.capabilities.authoring import DurableWorkPolicy
from due.django.capabilities.projection import DurableProjection
from due.django.capabilities.scheduled import CancellationResult, DurableScheduledAction
from due.django.evidence.attempts import (
    attempt_by_id,
    attempts_for,
    observe_effect_outcome,
    unsettled_work,
)
from due.django.models import DurableWorkState, EffectAttemptOutcome
from due.django.runtime import coordination
from due.django.runtime.coordination import claim_work, supersede_work
from due.django.runtime.metrics import DurableWorkOperation
from due.django.testing import WorkerDied, make_due
from tests.harness.evidence import EvidenceWork, Provider
from tests.harness.evidence import evidence_tables as evidence_tables
from tests.harness.primitive import (
    PrimitiveOwner,
    PrimitiveWork,
)
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)


class Scheduled(DurableScheduledAction[PrimitiveOwner, EvidenceWork]):
    def is_still_needed(self, work: EvidenceWork) -> bool:
        return True

    def perform_effect(self, work: EvidenceWork) -> None:
        Provider.write()


class Projection(DurableProjection[PrimitiveOwner, EvidenceWork]):
    def write_projection(self, work: EvidenceWork) -> None:
        Provider.write()


def record(capability: Scheduled | Projection) -> EvidenceWork:
    owner = PrimitiveOwner.objects.create()
    with capability.atomic(owner.pk, notify_worker=False) as pending:
        return pending.request_work(EvidenceWork(universe="acme", run_at=timezone.now()))


def capability_for(mode: str) -> Scheduled | Projection:
    return (Scheduled if mode == "scheduled" else Projection).for_owner(
        owner=PrimitiveOwner,
        work=EvidenceWork,
        policy=DurableWorkPolicy(retention=None, retry_delay=timedelta(0)),
    )


@pytest.mark.usefixtures("evidence_tables")
def test_imported_legacy_projection_write_remains_repairable(monkeypatch: pytest.MonkeyPatch) -> None:
    remote: list[datetime] = []

    def write(self: Projection, work: EvidenceWork) -> None:
        remote.append(work.run_at)  # External service applies the exact captured value.

    monkeypatch.setattr(Projection, "write_projection", write)
    capability = capability_for("projection")
    assert isinstance(capability, Projection)
    work = record(capability)
    assert work.owner_id is not None
    with capability.atomic(work.owner_id, notify_worker=False):
        capability.record_unknown_effect_outcome(work)
    escaped = attempts_for(work).get()
    assert escaped.desired_revision < work.desired_revision
    assert escaped.outcome == EffectAttemptOutcome.UNKNOWN

    assert capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.READY  # Current success did not cancel the old remote write.
    assert remote == [work.run_at]
    remote.append(work.run_at - timedelta(hours=1))  # The pre-upgrade provider request finally applies.
    make_due(work)
    assert capability.attempt_execution(work.pk)
    assert remote[-1] == work.run_at
    escaped.refresh_from_db()
    assert escaped.repair_pending and escaped.outcome == EffectAttemptOutcome.UNKNOWN

    # Only a definite receipt resolves uncertainty, through the existing API.
    observe_effect_outcome(escaped, EffectAttemptOutcome.APPLIED)
    make_due(work)
    assert capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    capability.derive()
    assert not capability.attempt_execution(work.pk)


@pytest.mark.usefixtures("evidence_tables")
def test_projection_uncertainty_import_requires_transaction_and_rolls_back() -> None:
    capability = capability_for("projection")
    assert isinstance(capability, Projection)
    work = record(capability)
    with pytest.raises(RuntimeError, match="inside.*transaction"):
        capability.record_unknown_effect_outcome(work)
    with transaction.atomic():
        capability.record_unknown_effect_outcome(work)
        assert attempts_for(work).exists()
        transaction.set_rollback(True)
    assert not attempts_for(work).exists()


@pytest.mark.usefixtures("evidence_tables")
@pytest.mark.parametrize("history", ["claimed", "executed", "already_imported"])
def test_projection_uncertainty_import_cannot_rewrite_existing_history(history: str) -> None:
    capability = capability_for("projection")
    assert isinstance(capability, Projection)
    work = record(capability)
    if history == "claimed":
        assert claim_work(type(work), work.pk, lease_duration=capability.policy.lease_duration) is not None
    elif history == "executed":
        assert capability.attempt_execution(work.pk)
    else:
        with transaction.atomic():
            capability.record_unknown_effect_outcome(work)
    before = list(attempts_for(work).values())
    with transaction.atomic(), pytest.raises(ValueError, match="unexecuted"):
        capability.record_unknown_effect_outcome(work)
    assert list(attempts_for(work).values()) == before


@pytest.mark.usefixtures("evidence_tables")
@pytest.mark.parametrize("mode", ["scheduled", "projection"])
@pytest.mark.parametrize(
    "history", ["never_entered", "worker_death", "timeout_success", "timeout_exhaustion", "success"]
)
def test_unsettled_query_follows_real_mode_history(mode: str, history: str, monkeypatch: pytest.MonkeyPatch) -> None:
    capability = capability_for(mode)
    if history == "timeout_exhaustion":
        capability = replace(capability, policy=replace(capability.policy, max_rearms=0))
    work = record(capability)
    scoped = EvidenceWork.objects.filter(pk=work.pk, universe=work.universe)
    assert unsettled_work(scoped).exists()  # queued intent blocks entry even before I/O
    if history in ("worker_death", "timeout_success", "timeout_exhaustion"):
        failure = WorkerDied if history == "worker_death" else TimeoutError
        monkeypatch.setattr(Provider, "failure", failure)
        with pytest.raises(failure):
            capability.attempt_execution(work.pk)
        assert attempts_for(work).get().outcome == EffectAttemptOutcome.UNKNOWN
        if history == "timeout_success":
            monkeypatch.setattr(Provider, "failure", None)
            make_due(work)
            assert capability.attempt_execution(work.pk)
    elif history == "success":
        assert capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.has_possible_effect() is (history != "never_entered")
    expected = history != "success"
    assert unsettled_work(scoped).exists() is expected
    if history == "timeout_exhaustion":
        assert work.state == DurableWorkState.EXHAUSTED
    if history == "timeout_success":
        # No fabricated observation: coverage belongs to the mode's settlement.
        assert attempts_for(work).filter(outcome=EffectAttemptOutcome.UNKNOWN).exists()
    assert not unsettled_work(scoped.filter(universe="another-universe")).exists()


@pytest.mark.usefixtures("evidence_tables")
def test_old_projection_revision_still_blocks_after_supersession(monkeypatch: pytest.MonkeyPatch) -> None:
    capability = capability_for("projection")
    work = record(capability)
    monkeypatch.setattr(Provider, "failure", TimeoutError)
    with pytest.raises(TimeoutError):
        capability.attempt_execution(work.pk)
    attempt = attempts_for(work).get()
    supersede_work(EvidenceWork, work.pk)
    monkeypatch.setattr(Provider, "failure", None)
    make_due(work)
    with patch.object(coordination, "record_work_operation") as operations:
        assert capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.READY
    reported = [call.args[1] for call in operations.call_args_list]
    assert DurableWorkOperation.CONTINUED in reported
    assert DurableWorkOperation.SUCCEEDED not in reported
    assert attempt.desired_revision < work.desired_revision
    assert unsettled_work(EvidenceWork.objects.filter(pk=work.pk)).exists()
    assert attempt_by_id(EvidenceWork, attempt.pk) == attempt
    with pytest.raises(type(attempt).DoesNotExist):
        attempt_by_id(PrimitiveWork, attempt.pk)


@pytest.mark.usefixtures("evidence_tables")
def test_cancelled_before_entry_has_no_remaining_obligation() -> None:
    capability = capability_for("scheduled")
    assert isinstance(capability, Scheduled)
    work = record(capability)
    assert work.owner_id is not None
    assert capability.cancel_pending(work.owner_id) == CancellationResult.CANCELLED
    assert not unsettled_work(EvidenceWork.objects.filter(pk=work.pk)).exists()


@pytest.mark.usefixtures("evidence_tables")
@pytest.mark.parametrize("resolve", [False, True])
def test_scheduled_compaction_preserves_uncertainty_and_started_identity(
    resolve: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability = capability_for("scheduled")
    assert isinstance(capability, Scheduled)
    work = record(capability)
    monkeypatch.setattr(Provider, "failure", TimeoutError)
    with pytest.raises(TimeoutError):
        capability.attempt_execution(work.pk)
    first = attempts_for(work).get()
    old = timezone.now() - timedelta(days=30)
    attempts_for(work).filter(pk=first.pk).update(started_at=old)
    if resolve:
        observe_effect_outcome(first, EffectAttemptOutcome.APPLIED)
        monkeypatch.setattr(Provider, "failure", None)
        make_due(work)
        assert capability.attempt_execution(work.pk)
        attempts_for(work).filter(pk=first.pk).update(observed_at=old + timedelta(seconds=2))
        attempts_for(work).exclude(pk=first.pk).filter(outcome=EffectAttemptOutcome.APPLIED).update(
            started_at=old + timedelta(seconds=1), observed_at=old + timedelta(seconds=2)
        )
    capability.derive()  # the same bounded compaction invoked by ordinary recovery
    remaining = attempts_for(work).get()
    if resolve:
        assert remaining.outcome == EffectAttemptOutcome.APPLIED
        assert remaining.pk != first.pk
        assert work.owner_id is not None
        assert capability.cancel_pending(work.owner_id) == CancellationResult.ALREADY_STARTED
    else:
        assert remaining.pk == first.pk
        assert unsettled_work(EvidenceWork.objects.filter(pk=work.pk)).exists()


@pytest.mark.usefixtures("evidence_tables")
def test_only_old_revision_evidence_blocks_exhausted_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    from due.errors import EffectNotAppliedError

    capability = capability_for("projection")
    capability = replace(capability, policy=replace(capability.policy, max_rearms=0))
    work = record(capability)
    monkeypatch.setattr(Provider, "failure", TimeoutError)
    with pytest.raises(TimeoutError):
        capability.attempt_execution(work.pk)
    supersede_work(EvidenceWork, work.pk)
    monkeypatch.setattr(Provider, "failure", EffectNotAppliedError)
    make_due(work)
    with pytest.raises(EffectNotAppliedError):
        capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.EXHAUSTED
    assert not attempts_for(work).filter(desired_revision=work.desired_revision, repair_pending=True).exists()
    assert unsettled_work(EvidenceWork.objects.filter(pk=work.pk)).exists()


@pytest.mark.usefixtures("evidence_tables")
def test_same_revision_uncertainty_stops_rewrites_but_protects_future_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote: list[datetime] = []

    def write_projection(self: Projection, work: EvidenceWork) -> None:
        Provider.write()
        remote.append(work.run_at)

    monkeypatch.setattr(Projection, "write_projection", write_projection)
    capability = capability_for("projection")
    work = record(capability)
    old_value = work.run_at
    monkeypatch.setattr(Provider, "failure", TimeoutError)
    with pytest.raises(TimeoutError):
        capability.attempt_execution(work.pk)
    escaped = attempts_for(work).get()
    monkeypatch.setattr(Provider, "failure", None)
    make_due(work)
    assert capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert remote == [old_value]
    # An escaped identical write cannot change the current projection. Keep its
    # evidence without paying for perpetual identical writes or inventing a receipt.
    capability.derive()
    assert not capability.attempt_execution(work.pk)
    escaped.refresh_from_db()
    assert escaped.outcome == EffectAttemptOutcome.UNKNOWN
    assert escaped.repair_pending
    assert work.owner_id is not None
    new_value = old_value + timedelta(hours=1)
    with capability.atomic(work.owner_id, notify_worker=False) as pending:
        pending.request_work(EvidenceWork(universe=work.universe, run_at=new_value))
    assert capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.READY
    assert remote[-1] == new_value
    remote.append(old_value)  # The escaped request lands after the newer revision.
    make_due(work)
    assert capability.attempt_execution(work.pk)
    assert remote[-1] == new_value
    # Once the late request is definitely finished, one following write settles.
    observe_effect_outcome(escaped, EffectAttemptOutcome.APPLIED)
    make_due(work)
    assert capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    capability.derive()
    assert not capability.attempt_execution(work.pk)


@pytest.mark.usefixtures("evidence_tables")
@pytest.mark.parametrize("mode", ["scheduled", "projection"])
def test_late_identical_receipt_becomes_prunable_without_another_write(
    mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability = capability_for(mode)
    work = record(capability)
    monkeypatch.setattr(Provider, "failure", TimeoutError)
    with pytest.raises(TimeoutError):
        capability.attempt_execution(work.pk)
    escaped = attempts_for(work).get()
    monkeypatch.setattr(Provider, "failure", None)
    make_due(work)
    assert capability.attempt_execution(work.pk)
    capability.derive()
    assert unsettled_work(EvidenceWork.objects.filter(pk=work.pk)).exists()
    observe_effect_outcome(escaped, EffectAttemptOutcome.APPLIED)
    capability.derive()
    escaped.refresh_from_db()
    assert not escaped.repair_pending
    assert not capability.attempt_execution(work.pk)
    assert attempts_for(work).count() == 2
    attempts_for(work).filter(pk=escaped.pk).update(observed_at=timezone.now() - timedelta(days=8))
    capability.derive()
    assert not attempts_for(work).filter(pk=escaped.pk).exists()
    assert attempts_for(work).count() == 1


@pytest.mark.no_db
@pytest.mark.parametrize("seconds", [0, -1])
def test_repair_window_requires_positive_duration(seconds: int) -> None:
    with pytest.raises(ValueError, match="max_repair_window"):
        DurableWorkPolicy(max_repair_window=timedelta(seconds=seconds))


@pytest.mark.no_db
def test_nonrepeatable_and_plain_replay_modes_refuse_repair_expiry() -> None:
    from due.django.capabilities.authoring import ReplaySafeEffect
    from due.django.capabilities.operations import DurableExternalOperation
    from tests.harness.enrollment import TEST_OWNED_CAPABILITIES

    for capability in TEST_OWNED_CAPABILITIES:
        if isinstance(capability, (DurableExternalOperation, ReplaySafeEffect)):
            with pytest.raises(ValueError, match="only supported"):
                replace(capability, policy=replace(capability.policy, max_repair_window=timedelta(hours=24)))
