"""
A settlement wakes existing dependent work in its own transaction, once per evidence-bearing outcome.

Design and lock order: docs/architecture.md#waking-dependent-work.
"""

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from pytest_obligation.integrations.django.lock_order import assert_consistent_lock_order, record_lock_order

from due.django.capabilities.projection import Projection
from due.django.capabilities.scheduled import ActionOutcome, ScheduledAction
from due.django.conformance.suite import DeliveryExample, assert_settlement_wakes_declared_dependents
from due.django.declarations.dependents import Dependent
from due.django.models import DurableWorkState
from due.django.public import DurableWorkPolicy, database_now
from due.django.runtime.coordination import WakeAction
from due.django.runtime.registry import (
    DurableWorkRegistration,
    index_durable_work_registrations,
)
from due.django.testing import hold_nudges, make_due
from due.errors import (
    EffectNotAppliedError,
    EffectOutcomeUnknownError,
    EffectRejection,
    WorkFailureDisposition,
)
from tests.harness import primitive as primitive_harness
from tests.harness.dependents import (
    WAKES_DEPENDENT,
    DependentWork,
    ReadOwner,
    claimed_cleanup,
    dependent,
    record_dependent,
)
from tests.harness.dependents import (
    dependent_tables_fixture as dependent_tables_fixture,
)
from tests.harness.evidence import EvidenceWork, Provider
from tests.harness.evidence import evidence_tables as evidence_tables
from tests.harness.operation import ProviderResult, Publication, PublicationWork
from tests.harness.operation import operation_environment as operation_environment
from tests.harness.operation import record as record_publication
from tests.harness.primitive import (
    PrimitiveEffect,
    PrimitiveOwner,
    PrimitiveWork,
    capture,
)
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = pytest.mark.usefixtures("dependent_tables")

DEPENDENT = DependentWork._meta.label_lower

upstream = PrimitiveEffect.for_owner(
    owner=PrimitiveOwner,
    work=PrimitiveWork,
    nudge=capture,
    policy=DurableWorkPolicy(retry_delay=timedelta(0), max_rearms=1),
    wakes=WAKES_DEPENDENT,
)


def owner_with_dependent() -> tuple[PrimitiveOwner, DependentWork]:
    owner = PrimitiveOwner.objects.create()
    return owner, record_dependent(owner)


def record_upstream(owner: PrimitiveOwner) -> PrimitiveWork:
    with upstream.atomic(owner.pk, notify_worker=False) as pending:
        return pending.request_work(PrimitiveWork(payload="after", universe="acme"))


def wakes(held: list[Any]) -> int:
    return sum(1 for nudge in held if nudge.work_name == DEPENDENT)


def arrange(work: DependentWork, state: DurableWorkState, *, available_at_offset: timedelta) -> None:
    # ARRANGE: put the row in each state the wake must handle; wake_work itself is under test.
    now = database_now()
    leased = state in (DurableWorkState.QUEUED, DurableWorkState.CLAIMED)
    finished = state in (DurableWorkState.SUCCEEDED, DurableWorkState.EXHAUSTED, DurableWorkState.TERMINAL_FAILED)
    DependentWork.objects.filter(pk=work.pk).update(
        state=state,
        rearm_count=2,
        available_at=now + available_at_offset,
        lease_expires_at=now + timedelta(minutes=5) if leased else None,
        completed_at=now if finished else None,
        **({"applied_revision": F("desired_revision")} if state == DurableWorkState.SUCCEEDED else {}),
    )


STATE_TABLE = [
    pytest.param(DurableWorkState.READY, timedelta(minutes=5), WakeAction.DUE, 0, 2, id="ready-in-backoff"),
    pytest.param(DurableWorkState.READY, -timedelta(seconds=1), WakeAction.DUE, 0, 2, id="ready-due"),
    pytest.param(DurableWorkState.RETRYABLE, timedelta(minutes=5), WakeAction.DUE, 0, 2, id="retryable"),
    pytest.param(DurableWorkState.QUEUED, -timedelta(seconds=1), WakeAction.QUEUED, 0, 2, id="queued"),
    pytest.param(DurableWorkState.CLAIMED, -timedelta(seconds=1), WakeAction.FENCED, 1, 2, id="claimed"),
    pytest.param(DurableWorkState.SUCCEEDED, timedelta(0), WakeAction.REOPENED, 0, 2, id="succeeded"),
    pytest.param(DurableWorkState.EXHAUSTED, timedelta(0), WakeAction.REARMED, 1, 0, id="exhausted"),
    pytest.param(DurableWorkState.TERMINAL_FAILED, timedelta(0), WakeAction.REARMED, 1, 0, id="terminal-failed"),
]


@pytest.mark.parametrize("notify_worker", [False, True])
@pytest.mark.parametrize("state, offset, action, revision_delta, rearm_count", STATE_TABLE)
def test_wake_follows_the_state_table(state, offset, action, revision_delta, rearm_count, notify_worker) -> None:
    _, work = owner_with_dependent()
    arrange(work, state, available_at_offset=offset)
    work.refresh_from_db()
    with hold_nudges() as held:
        assert dependent.wake(work.pk, notify_worker=notify_worker) is action
    woken = DependentWork.objects.get(pk=work.pk)
    assert woken.desired_revision == work.desired_revision + revision_delta
    assert woken.rearm_count == rearm_count
    if action is WakeAction.QUEUED:
        assert (woken.state, woken.available_at) == (work.state, work.available_at)
        assert wakes(held) == 0
    else:
        assert woken.state in (DurableWorkState.READY, DurableWorkState.RETRYABLE)
        assert woken.available_at <= database_now()
        assert wakes(held) == int(notify_worker)


def test_wake_rolls_back_with_its_callers_transaction() -> None:
    _, work = owner_with_dependent()
    arrange(work, DurableWorkState.SUCCEEDED, available_at_offset=timedelta(0))
    with hold_nudges() as held, pytest.raises(RuntimeError, match="settlement failed"):
        with transaction.atomic():
            dependent.wake(work.pk)
            raise RuntimeError("settlement failed")
    assert DependentWork.objects.get(pk=work.pk).state == DurableWorkState.SUCCEEDED
    assert wakes(held) == 0


def test_a_missing_dependent_fails_the_settlement_that_owes_it() -> None:
    with pytest.raises(DependentWork.DoesNotExist):
        dependent.wake(10**9)


def test_settlement_wakes_the_dependent_which_reads_the_settled_fact() -> None:
    owner, work = owner_with_dependent()
    with hold_nudges():  # ARRANGE: the dependent has already run once and finished.
        dependent.enqueue_execution(work.pk)
    assert dependent.attempt_execution(*ReadOwner.deliveries[-1])
    assert ReadOwner.settled == ["before"]
    source = record_upstream(owner)
    with hold_nudges() as held:
        assert upstream.attempt_execution(source.pk)
    assert [nudge.work_id for nudge in held if nudge.work_name == DEPENDENT] == [work.pk]
    # The notification the settlement published, delivered: no recovery tick.
    dependent.enqueue_execution(work.pk)
    assert dependent.attempt_execution(*ReadOwner.deliveries[-1])
    assert ReadOwner.settled == ["before", "after"]


def test_a_queued_delivery_survives_a_wake_and_reads_the_new_fact() -> None:
    owner, work = owner_with_dependent()
    dependent.enqueue_execution(work.pk)
    _, token = ReadOwner.deliveries[-1]
    with hold_nudges() as held:
        assert upstream.attempt_execution(record_upstream(owner).pk)
    assert wakes(held) == 0
    assert dependent.attempt_execution(work.pk, token)
    assert ReadOwner.settled == ["after"]


def test_a_pass_that_read_the_old_fact_is_fenced_and_cannot_settle() -> None:
    owner, work = owner_with_dependent()
    source = record_upstream(owner)
    ReadOwner.after_read = lambda: upstream.attempt_execution(source.pk)
    with hold_nudges() as held:
        assert not dependent.attempt_execution(work.pk)
    assert ReadOwner.settled == []
    fenced = DependentWork.objects.get(pk=work.pk)
    assert (fenced.state, fenced.desired_revision, fenced.rearm_count) == (
        DurableWorkState.READY,
        work.desired_revision + 1,
        work.rearm_count,
    )
    assert wakes(held) == 1
    assert dependent.attempt_execution(work.pk)
    assert ReadOwner.settled == ["after"]


def test_retries_do_not_wake_and_exhaustion_does(monkeypatch: pytest.MonkeyPatch) -> None:
    owner, _ = owner_with_dependent()
    source = record_upstream(owner)

    def fail(work: PrimitiveWork) -> str:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(primitive_harness, "call", fail)
    states = []
    with hold_nudges() as held:
        while True:
            with pytest.raises(RuntimeError, match="provider unavailable"):
                upstream.attempt_execution(source.pk)
            source.refresh_from_db()
            states.append((source.state, wakes(held)))
            if source.state == DurableWorkState.EXHAUSTED:
                break
            make_due(source)
    assert states[:-1] and all(state == (DurableWorkState.RETRYABLE, 0) for state in states[:-1])
    assert states[-1] == (DurableWorkState.EXHAUSTED, 1)


def test_terminal_failure_wakes(monkeypatch: pytest.MonkeyPatch) -> None:
    owner, _ = owner_with_dependent()
    source = record_upstream(owner)
    monkeypatch.setattr(primitive_harness, "call", lambda work: (_ for _ in ()).throw(RuntimeError("rejected")))
    monkeypatch.setattr(PrimitiveEffect, "classify_error", lambda self, error: WorkFailureDisposition.TERMINAL)
    with hold_nudges() as held, pytest.raises(RuntimeError, match="rejected"):
        upstream.attempt_execution(source.pk)
    source.refresh_from_db()
    assert (source.state, wakes(held)) == (DurableWorkState.TERMINAL_FAILED, 1)


def test_an_operation_wakes_once_per_outcome_live_unknown_and_receipt_repair(
    publication: Publication, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation = replace(publication, wakes=WAKES_DEPENDENT)
    applied = record_publication(operation)
    record_dependent(PrimitiveOwner.objects.get(pk=applied.owner_id))
    with hold_nudges() as held:
        assert operation.attempt_execution(applied.pk)
    assert wakes(held) == 1, "live settlement wakes once, not once per composed finalizer"

    uncertain = record_publication(operation)
    record_dependent(PrimitiveOwner.objects.get(pk=uncertain.owner_id))
    monkeypatch.setattr(Publication, "perform_effect", lambda self, work: (_ for _ in ()).throw(TimeoutError()))
    with hold_nudges() as held, pytest.raises(EffectOutcomeUnknownError):
        operation.attempt_execution(uncertain.pk)
    uncertain.refresh_from_db()
    assert (uncertain.state, wakes(held)) == (DurableWorkState.UNKNOWN, 1)
    assert operation.record_observed_effect_evidence(uncertain.pk, ProviderResult(value="late"))
    with hold_nudges() as held:
        operation.recover()
    uncertain.refresh_from_db()
    assert (uncertain.state, wakes(held)) == (DurableWorkState.SUCCEEDED, 1)


def test_an_operation_rejection_wakes(publication: Publication, monkeypatch: pytest.MonkeyPatch) -> None:
    operation = replace(publication, wakes=WAKES_DEPENDENT)
    work = record_publication(operation)
    record_dependent(PrimitiveOwner.objects.get(pk=work.owner_id))
    rejection = EffectRejection(message="definitely refused", disposition=WorkFailureDisposition.TERMINAL)
    monkeypatch.setattr(Publication, "failure", EffectNotAppliedError(rejection))
    with hold_nudges() as held, pytest.raises(EffectNotAppliedError):
        operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert (work.state, wakes(held)) == (DurableWorkState.TERMINAL_FAILED, 1)


class Feed(Projection[PrimitiveOwner, EvidenceWork]):
    def write_projection(self, work: EvidenceWork) -> None:
        Provider.write()


class Deadline(ScheduledAction[PrimitiveOwner, EvidenceWork]):
    def is_still_needed(self, work: EvidenceWork) -> bool:
        return True

    def perform_effect(self, work: EvidenceWork) -> None:
        Provider.write()

    def on_completed(self, work: EvidenceWork, outcome: ActionOutcome) -> None:
        """Simple callbacks run beside the declared wake, not instead of it."""


@pytest.mark.usefixtures("evidence_tables")
@pytest.mark.parametrize("implementation", [Feed, Deadline])
def test_simple_callback_modes_wake_on_success(implementation: type[Feed] | type[Deadline]) -> None:
    capability = implementation.for_owner(
        owner=PrimitiveOwner,
        work=EvidenceWork,
        input_fields=("universe", "run_at", "cancelled"),
        policy=DurableWorkPolicy(retention=None, retry_delay=timedelta(0)),
        wakes=WAKES_DEPENDENT,
    )
    owner, _ = owner_with_dependent()
    with capability.atomic(owner.pk, notify_worker=False) as pending:
        work = pending.request_work(EvidenceWork(universe="acme", run_at=timezone.now()))
    with hold_nudges() as held:
        assert capability.attempt_execution(work.pk)
    assert wakes(held) == 1


def test_upstream_locks_precede_the_dependent_row() -> None:
    owner, work = owner_with_dependent()
    with record_lock_order("dependent wake") as recorder:
        with hold_nudges():
            assert upstream.attempt_execution(record_upstream(owner).pk)
        dependent.enqueue_execution(work.pk)
        assert dependent.attempt_execution(*ReadOwner.deliveries[-1])
    owner_table, upstream_table, dependent_table = (
        model._meta.db_table for model in (PrimitiveOwner, PrimitiveWork, DependentWork)
    )
    assert any(
        dependent_table in item.tables
        and upstream_table in item.tables
        and item.tables.index(upstream_table) < item.tables.index(dependent_table)
        for item in recorder.sequences
    ), "positive control: settlement must lock the upstream work row and then wake the dependent"
    for item in recorder.sequences:
        if dependent_table in item.tables:
            ahead = item.tables[: item.tables.index(dependent_table)]
            assert upstream_table not in item.tables or upstream_table in ahead, item
            assert owner_table not in item.tables or owner_table in ahead, item
    assert_consistent_lock_order(recorder.sequences)


@pytest.mark.parametrize(
    "wakes, message",
    [
        pytest.param(lambda: (Dependent(PrimitiveWork, via=lambda work: work.pk),), "its own", id="self"),
        pytest.param(lambda: WAKES_DEPENDENT * 2, "once", id="duplicate"),
    ],
)
def test_declarations_refuse_self_and_duplicate_dependents(wakes, message) -> None:
    with pytest.raises(ValueError, match=message):
        replace(upstream, wakes=wakes())


def test_an_occurrence_cannot_be_declared_a_dependent() -> None:
    assert PublicationWork.requires_effect_evidence
    with pytest.raises(ValueError, match="occurrence cannot be superseded"):
        replace(upstream, wakes=(Dependent(PublicationWork, via=lambda work: work.pk),))


def registration(name: str, capability: Any) -> DurableWorkRegistration:
    return DurableWorkRegistration(name=name, capability=capability, queue="celery")


def test_startup_refuses_a_wake_no_registration_routes() -> None:
    with pytest.raises(ValueError, match="which no registration routes"):
        index_durable_work_registrations([registration("upstream", upstream)])
    index_durable_work_registrations([registration("upstream", upstream), registration("dependent", dependent)])


def test_startup_refuses_a_wake_of_work_its_producer_claims() -> None:
    with pytest.raises(ValueError, match="producer's claim owns"):
        index_durable_work_registrations([registration("upstream", upstream), registration("claimed", claimed_cleanup)])
    with pytest.raises(TypeError, match="cannot be woken"):
        claimed_cleanup.wake(1)


def primitive_delivery() -> DeliveryExample[PrimitiveWork, str]:
    owner, _ = owner_with_dependent()
    work = record_upstream(owner)
    return DeliveryExample(work=work, observe=lambda: PrimitiveOwner.objects.get(pk=owner.pk).value, expected="after")


def test_generated_proof_accepts_a_declared_wake() -> None:
    assert_settlement_wakes_declared_dependents(upstream, primitive_delivery)


def test_generated_proof_rejects_a_settlement_that_does_not_wake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(PrimitiveEffect, "_wake_dependents", lambda self, work: None)
    with pytest.raises(AssertionError, match="published 0 wake"):
        assert_settlement_wakes_declared_dependents(upstream, primitive_delivery)
