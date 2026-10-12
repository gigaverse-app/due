"""Product callbacks cannot discard receipt settlement or late-write repair."""

from datetime import timedelta

import pytest
from django.utils import timezone

from due.django.capabilities.authoring import DurableWorkPolicy
from due.django.capabilities.projection import Projection
from due.django.capabilities.scheduled import ActionOutcome, ScheduledAction
from due.django.evidence.attempts import attempts_for, unsettled_work
from due.django.models import DurableWorkState, EffectAttemptOutcome
from due.django.runtime.coordination import supersede_work
from due.django.testing import make_due
from tests.harness.evidence import (
    EvidenceWork,
    Provider,
)
from tests.harness.evidence import evidence_tables as evidence_tables
from tests.harness.primitive import PrimitiveOwner
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)


class Feed(Projection[PrimitiveOwner, EvidenceWork]):
    def write_projection(self, work: EvidenceWork) -> None:
        Provider.write()

    def on_projected(self, work: EvidenceWork) -> None:
        PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="published")


class Deadline(ScheduledAction[PrimitiveOwner, EvidenceWork]):
    def is_still_needed(self, work: EvidenceWork) -> bool:
        return not work.cancelled

    def perform_effect(self, work: EvidenceWork) -> None:
        Provider.write()

    def on_completed(self, work: EvidenceWork, outcome: ActionOutcome) -> None:
        PrimitiveOwner.objects.filter(pk=work.owner_id).update(value=outcome.value)


def bind(implementation):
    return implementation.for_owner(
        owner=PrimitiveOwner,
        work=EvidenceWork,
        input_fields=("universe", "run_at", "cancelled"),
        policy=DurableWorkPolicy(retention=None, retry_delay=timedelta(0)),
    )


def record(capability, *, cancelled=False):
    owner = PrimitiveOwner.objects.create()
    with capability.atomic(owner.pk, notify_worker=False) as pending:
        return pending.request_work(EvidenceWork(universe="acme", run_at=timezone.now(), cancelled=cancelled))


pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("evidence_tables")]


@pytest.mark.parametrize("implementation", [Feed, Deadline])
@pytest.mark.parametrize("older_timeout", [False, True])
def test_callback_settles_receipts_without_super_and_preserves_unknown(implementation, older_timeout, monkeypatch):
    capability = bind(implementation)
    work = record(capability)
    if older_timeout:
        monkeypatch.setattr(Provider, "failure", TimeoutError)
        with pytest.raises(TimeoutError):
            capability.attempt_execution(work.pk)
        supersede_work(EvidenceWork, work.pk)
        monkeypatch.setattr(Provider, "failure", None)
        make_due(work)
    assert capability.attempt_execution(work.pk)
    work.refresh_from_db()
    expects_repair = older_timeout and implementation is Feed
    assert work.state == (DurableWorkState.READY if expects_repair else DurableWorkState.SUCCEEDED)
    assert not attempts_for(work).filter(outcome=EffectAttemptOutcome.APPLIED, repair_pending=True).exists()
    assert unsettled_work(EvidenceWork.objects.filter(pk=work.pk), strict=True).exists() is older_timeout
    expected = "published" if implementation is Feed else "performed"
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == expected


@pytest.mark.parametrize("implementation, callback", [(Feed, "on_projected"), (Deadline, "on_completed")])
@pytest.mark.parametrize("fault", ["raise", "return_value"])
def test_invalid_callback_rolls_back_product_and_settlement(implementation, callback, fault, monkeypatch):
    capability = bind(implementation)
    work = record(capability)

    def fail(self, work, *args):
        PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="must roll back")
        if fault == "raise":
            raise RuntimeError("product update failed")
        return True

    monkeypatch.setattr(implementation, callback, fail)
    with pytest.raises((RuntimeError, TypeError)):
        capability.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state != DurableWorkState.SUCCEEDED
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "before"
    assert attempts_for(work).filter(outcome=EffectAttemptOutcome.APPLIED, repair_pending=True).count() == 1


@pytest.mark.parametrize("implementation", [Feed, Deadline])
def test_stale_execution_cannot_call_product_callback(implementation, monkeypatch):
    capability = bind(implementation)
    work = record(capability)
    monkeypatch.setattr(Provider, "write", lambda: supersede_work(EvidenceWork, work.pk))
    assert not capability.attempt_execution(work.pk)
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "before"
    assert attempts_for(work).filter(outcome=EffectAttemptOutcome.APPLIED, repair_pending=True).exists()


def test_scheduled_noop_is_explicit_and_never_calls_provider(monkeypatch):
    capability = bind(Deadline)
    work = record(capability, cancelled=True)
    monkeypatch.setattr(Provider, "failure", AssertionError)
    assert capability.attempt_execution(work.pk)
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == ActionOutcome.NO_LONGER_NEEDED.value
    assert not attempts_for(work).exists()


@pytest.mark.parametrize("implementation", [Feed, Deadline])
@pytest.mark.parametrize("method", ["finalize_success", "_finalize_success", "apply", "on_success"])
def test_simple_mode_rejects_rich_settlement_overrides(implementation, method, monkeypatch):
    monkeypatch.setattr(implementation, method, lambda *args: None)
    with pytest.raises(TypeError, match="runtime-owned"):
        bind(implementation)


@pytest.mark.parametrize("implementation", [Feed, Deadline])
@pytest.mark.parametrize("older_timeout", [False, True])
def test_shared_race_proof_observes_simple_callbacks_and_preserves_repair(implementation, older_timeout, monkeypatch):
    from dataclasses import replace

    from due.django.conformance.concurrency import assert_competing_deliveries

    capability = replace(bind(implementation), nudge=lambda *args: None)
    work = record(capability)
    if older_timeout:
        monkeypatch.setattr(Provider, "failure", TimeoutError)
        with pytest.raises(TimeoutError):
            capability.attempt_execution(work.pk)
        supersede_work(EvidenceWork, work.pk)
        monkeypatch.setattr(Provider, "failure", None)
        make_due(work)
    capability.enqueue_execution(work.pk)
    assert_competing_deliveries(
        capability,
        work,
        observe=lambda: PrimitiveOwner.objects.get(pk=work.owner_id).value,
        expected="published" if implementation is Feed else "performed",
    )
    work.refresh_from_db()
    assert work.state == (
        DurableWorkState.READY if implementation is Feed and older_timeout else DurableWorkState.SUCCEEDED
    )
    assert unsettled_work(EvidenceWork.objects.filter(pk=work.pk), strict=True).exists() is older_timeout
