"""Read-only provider observation composes existing delivery and evidence authority."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from django.core.exceptions import ValidationError
from pytest_obligation.poison import PoisonSwitch, assert_poison_is_isolated

from due.django.conformance.background import BackgroundWork
from due.django.conformance.background import background_work as background_work
from due.django.conformance.background import scheduler_clock as scheduler_clock
from due.django.evidence.attempts import attempts_for
from due.django.integration import current_tenant as get_current_universe
from due.django.integration import current_universe_scope
from due.django.models import DurableWorkState, EffectAttemptOutcome
from due.django.public import AppliedEvidenceContract, Discovery
from due.django.runtime.tasks import observe_durable_work_task
from tests.harness.observation import (
    ObservationProvider,
    ObservedPublication,
    ObservedWork,
    uncertain_work,
)
from tests.harness.observation import observer as observer
from tests.harness.operation import ProviderResult
from tests.harness.primitive import PrimitiveOwner

pytestmark = pytest.mark.django_db(transaction=True)


def test_recovery_reserves_without_provider_io_and_duplicate_delivery_reads_once(
    observer: ObservedPublication, background_work: BackgroundWork
) -> None:
    work = uncertain_work(observer)
    with observer.atomic(PrimitiveOwner.objects.create().pk, notify_worker=False) as pending:
        ready = pending.request_work(ObservedWork(payload="next", universe="acme"))
    background_work.hold()
    observer.recover()
    observer.recover()
    # Regular work is dispatched even while observation delivery is held.
    assert [delivery.args[1] for delivery in background_work.work_deliveries("observation_self_test")] == [ready.pk]
    deliveries = background_work.dispatched(observe_durable_work_task)
    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert delivery.name == observe_durable_work_task.name
    assert ObservationProvider.reads == []
    ObservationProvider.result = ProviderResult(value="applied")
    assert observe_durable_work_task(*delivery.args)
    assert not observe_durable_work_task(*delivery.args)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "applied"
    assert ObservationProvider.reads == [(work.pk, "acme")]
    assert ObservationProvider.creates == [work.pk]


def test_poisoned_discovery_owner_does_not_stop_observation_recovery(
    observer: ObservedPublication, background_work: BackgroundWork
) -> None:
    work = uncertain_work(observer)
    poisoned = PrimitiveOwner.objects.create()
    capability = replace(
        observer,
        discovery=Discovery(
            unbounded_because="Only the explicitly selected fixture owner is a candidate.",
            candidates=lambda: PrimitiveOwner.objects.filter(pk=poisoned.pk).order_by("pk"),
            # A blank payload fails full_clean: this owner's fact can never become valid work.
            desired_for=lambda owner, existing: ObservedWork(payload="", universe="acme"),
            page_size=1,
            applied_evidence=AppliedEvidenceContract.CANONICAL_OWNER_STATE,
        ),
    )
    background_work.hold()
    with pytest.raises(ExceptionGroup) as raised:
        capability.recover()
    assert raised.group_contains(ValidationError, match="cannot be blank")
    # The observation step that follows discovery in the same pass still
    # reserved and published the UNKNOWN work.
    assert [delivery.args[1] for delivery in background_work.dispatched(observe_durable_work_task)] == [work.pk]


@pytest.mark.parametrize("outage", [False, True], ids=["inconclusive", "unavailable"])
def test_inconclusive_observation_preserves_uncertainty_and_rotates_pages(
    observer: ObservedPublication, background_work: BackgroundWork, outage: bool
) -> None:
    works = [uncertain_work(observer) for _ in range(3)]
    ObservationProvider.failure = OSError("inventory down") if outage else None
    background_work.run_recovery("observation_self_test")
    assert len(ObservationProvider.reads) == observer.policy.batch_size
    background_work.run_recovery("observation_self_test")
    assert {identity for identity, _ in ObservationProvider.reads} == {work.pk for work in works}
    assert len(ObservationProvider.reads) == 3
    assert set(ObservedWork.objects.values_list("state", flat=True)) == {DurableWorkState.UNKNOWN}
    for work in works:
        work.refresh_from_db()
        assert work.rearm_count == 0
        assert attempts_for(work).get().outcome == EffectAttemptOutcome.UNKNOWN
    ObservationProvider.failure = None
    ObservationProvider.result = ProviderResult(value="applied")
    background_work.advance(31)
    background_work.run_recovery("observation_self_test")
    background_work.run_recovery("observation_self_test")
    assert set(ObservedWork.objects.values_list("state", flat=True)) == {DurableWorkState.SUCCEEDED}
    assert ObservationProvider.creates == [work.pk for work in works]


def test_lost_observation_delivery_recovers_and_rejects_its_old_token(
    observer: ObservedPublication, background_work: BackgroundWork
) -> None:
    work = uncertain_work(observer)
    background_work.hold()
    observer.recover()
    lost = background_work.work_deliveries("observation_self_test", include_observations=True)[0]
    assert background_work.clock is not None
    background_work.clock.shift(observer.policy.lease_duration.total_seconds() + 1)
    background_work.hold()
    observer.recover()
    replacement = background_work.work_deliveries("observation_self_test", include_observations=True)[-1]
    ObservationProvider.result = ProviderResult(value="applied")
    assert not observe_durable_work_task(*lost.args)
    assert observe_durable_work_task(*replacement.args)
    assert ObservationProvider.reads == [(work.pk, "acme")]


def test_webhook_settlement_before_observation_delivery_skips_provider_read(
    observer: ObservedPublication, background_work: BackgroundWork
) -> None:
    work = uncertain_work(observer)
    background_work.hold()
    observer.recover()
    assert observer.record_observed_effect_evidence(work.pk, ProviderResult(value="webhook"))
    assert observer.settle_from_applied_effect_evidence(work.pk)
    background_work.release()
    assert ObservationProvider.reads == []
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "webhook"


def test_observation_restores_tenant_scope(observer: ObservedPublication, background_work: BackgroundWork) -> None:
    work = uncertain_work(observer, universe="beta")
    with current_universe_scope("acme"):
        background_work.run_recovery("observation_self_test")
        assert get_current_universe() == "acme"
    assert ObservationProvider.reads == [(work.pk, "beta")]


def test_observation_lease_must_outlive_worker(observer: ObservedPublication) -> None:
    with pytest.raises(ValueError, match="observation lease"):
        replace(observer, policy=replace(observer.policy, lease_duration=timedelta(seconds=60)))


def test_observation_query_and_installed_index_are_shared_contracts(observer: ObservedPublication) -> None:
    from django.db import connection
    from pytest_obligation.integrations.django.selection import DjangoSelectionInspector

    from due.django.conformance.indexes import assert_work_indexes

    assert_work_indexes(observer.model)
    due = observer._observation_lease.available(observer.observations_due())[: observer.policy.batch_size]
    served, explanation = DjangoSelectionInspector(partial_indexes={"observed_test_observe_ix"}).index_served(due)
    assert served, explanation
    index = next(index for index in observer.model._meta.indexes if index.name == "observed_test_observe_ix")
    with connection.schema_editor() as editor:
        editor.remove_index(observer.model, index)
    with pytest.raises(AssertionError, match="missing valid shared index.*observe"):
        assert_work_indexes(observer.model)


def test_observation_duplicate_workers_share_one_provider_read(
    observer: ObservedPublication, background_work: BackgroundWork, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from django.db import connection

    from due.django.conformance.worker_connection import database_worker_connection

    work = uncertain_work(observer)
    background_work.hold()
    observer.recover()
    delivery = background_work.dispatched(observe_durable_work_task)[0]
    entered, release = Event(), Event()
    calls: list[int] = []

    def held_observation(identity: int) -> ProviderResult:
        assert not connection.in_atomic_block
        calls.append(identity)
        entered.set()
        assert release.wait(10)
        return ProviderResult(value="applied")

    def deliver() -> bool:
        with database_worker_connection():
            return observe_durable_work_task(*delivery.args)

    monkeypatch.setattr(ObservationProvider, "observe", held_observation)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(deliver)
        try:
            assert entered.wait(10)
            assert pool.submit(deliver).result(timeout=10) is False
        finally:
            release.set()
        assert first.result(timeout=10) is True
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert calls == [work.pk]
    assert ObservationProvider.creates == [work.pk]


@pytest.mark.parametrize("failure_stage", ["contradictory_evidence", "product_settlement"])
def test_observation_surfaces_local_failure_and_repairs_from_retained_receipt(
    observer: ObservedPublication,
    background_work: BackgroundWork,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    work = uncertain_work(observer)
    background_work.hold()
    observer.recover()
    delivery = background_work.dispatched(observe_durable_work_task)[0]
    original = ProviderResult(value="accepted")
    finalize_applied = ObservedPublication.finalize_applied
    if failure_stage == "contradictory_evidence":
        assert observer.record_observed_effect_evidence(work.pk, original)
        ObservationProvider.result = ProviderResult(value="contradiction")
        error_type = ValueError
    else:
        ObservationProvider.result = original
        error_type = RuntimeError

        def fail_publication(self: ObservedPublication, work: ObservedWork, result: ProviderResult) -> None:
            raise RuntimeError("product publication unavailable")

        monkeypatch.setattr(ObservedPublication, "finalize_applied", fail_publication)
    with pytest.raises(error_type):
        observe_durable_work_task(*delivery.args)
    work.refresh_from_db()
    assert work.state == DurableWorkState.UNKNOWN
    assert work.observation_claimed_at is None
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "before"
    attempt = attempts_for(work).get()
    assert attempt.outcome == EffectAttemptOutcome.APPLIED and attempt.repair_pending
    assert observer.evidence_store.read(attempt) == original

    monkeypatch.setattr(ObservedPublication, "finalize_applied", finalize_applied)
    observer.recover()
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == original.value
    assert ObservationProvider.reads == [(work.pk, "acme")]
    assert ObservationProvider.creates == [work.pk]


def test_one_refused_observation_publication_does_not_hold_back_the_next(
    observer: ObservedPublication, background_work: BackgroundWork, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The observation loop is library code; the primitive proves its per-identity isolation once."""
    bad, good = uncertain_work(observer), uncertain_work(observer)
    refusals = PoisonSwitch()
    original = ObservedPublication._publish_observation

    def publish(self: ObservedPublication, work_id: int, token: datetime) -> None:
        # EXTERNAL SEAM: the broker refuses this one work id's observation message.
        refusals.check(work_id, "broker refused this observation")
        original(self, work_id, token)

    monkeypatch.setattr(ObservedPublication, "_publish_observation", publish)
    background_work.hold()

    def still_unknown() -> bool:
        bad.refresh_from_db()
        return bad.state == DurableWorkState.UNKNOWN

    assert_poison_is_isolated(
        name="observed publication/observation",
        poison=refusals.poisoned(bad.pk),
        run_tick=observer.recover,
        progressed=lambda: (
            good.pk in [delivery.args[1] for delivery in background_work.dispatched(observe_durable_work_task)]
        ),
        still_owed=still_unknown,
    )
