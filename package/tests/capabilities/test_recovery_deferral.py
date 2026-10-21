"""
A poisoned identity is held out with backoff, so it cannot starve the page behind it.

Isolation alone keeps one failing identity from stopping the rest of its page
and the later phases. But selection is oldest first: the same identity leads
every page, so when a page holds nothing else (page size 1 here, or a page
full of poisoned rows in production) the identities behind it are never
reached. Deferral holds the isolated identity out of its phase until
``retry_at``; it stays owed in its own table and completes once healthy.

Design: docs/architecture.md.
"""

import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from django.core.exceptions import ValidationError
from django.db import DatabaseError, OperationalError, connection, models, transaction
from django.db.models import QuerySet
from django.test.utils import CaptureQueriesContext

from due.django.models import RecoveryDeferral
from due.django.public import AppliedEvidenceContract, Discovery, DurableWorkState
from due.django.runtime import recovery_pass
from due.django.runtime.coordination import claim_work, database_now
from due.django.runtime.metrics import RecoveryPhase
from due.django.runtime.recovery_pass import (
    DEFERRAL_CAP,
    DEFERRAL_FORGET_AFTER,
    RecoveryIncomplete,
    RecoveryPass,
    defer_identity,
    deferral_backoff,
    deferral_identity_problem,
    publish_recovery_deferrals,
)
from due.django.testing import end_recovery_holds
from tests.harness.primitive import (
    PrimitiveEffect,
    PrimitiveOwner,
    PrimitiveWork,
    primitive,
    record,
)
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]

EFFECT = PrimitiveWork._meta.label_lower
#: Reaped, and possibly already re-dispatched by the same tick (zero retry delay).
REAPED = (DurableWorkState.RETRYABLE, DurableWorkState.QUEUED)


def _one_owner_per_page(
    owners: list[PrimitiveOwner],
    poisoned: set[int],
    *,
    page_size: int = 1,
    candidates: Callable[[], QuerySet[PrimitiveOwner]] | None = None,
):
    def desired_for(owner: PrimitiveOwner, existing: PrimitiveWork | None) -> PrimitiveWork:
        # A blank payload fails full_clean: the owner's fact cannot become valid work.
        return PrimitiveWork(payload="" if owner.pk in poisoned else "derived", universe="acme")

    return replace(
        primitive,
        discovery=Discovery(
            unbounded_because="Only explicitly selected synthetic fixture owners are candidates.",
            candidates=candidates
            or (lambda: PrimitiveOwner.objects.filter(pk__in=[owner.pk for owner in owners]).order_by("pk")),
            desired_for=desired_for,
            page_size=page_size,
            applied_evidence=AppliedEvidenceContract.CANONICAL_OWNER_STATE,
        ),
    )


def _end_the_hold(phase: RecoveryPhase, identity: object) -> None:
    # ARRANGE: time passes until the hold ends; recovery still makes every transition.
    RecoveryDeferral.objects.filter(effect=EFFECT, phase=phase, identity=str(identity)).update(
        retry_at=database_now() - timedelta(seconds=1)
    )


def test_a_poisoned_discovery_owner_no_longer_starves_the_owner_behind_it():
    first, behind = PrimitiveOwner.objects.create(), PrimitiveOwner.objects.create()
    poisoned = {first.pk}
    capability = _one_owner_per_page([first, behind], poisoned)

    with pytest.raises(ExceptionGroup) as raised:
        capability.recover()
    assert raised.group_contains(ValidationError)
    hold = RecoveryDeferral.objects.get(effect=EFFECT, phase=RecoveryPhase.DISCOVERY, identity=str(first.pk))
    assert hold.failures == 1 and hold.retry_at > database_now()
    assert not PrimitiveWork.objects.filter(owner=behind).exists(), "page size 1: the first tick only saw the poison"

    capability.recover()  # The poisoned owner is held out, so the page reaches the owner behind it.
    assert PrimitiveWork.objects.filter(owner=behind, payload="derived").exists()
    assert not PrimitiveWork.objects.filter(owner=first).exists(), "the poisoned owner stays owed, not invented"

    # Positive control: once healthy and past its hold, the deferred owner completes and its hold clears.
    poisoned.clear()
    _end_the_hold(RecoveryPhase.DISCOVERY, first.pk)
    capability.recover()
    assert PrimitiveWork.objects.filter(owner=first, payload="derived").exists()
    assert not RecoveryDeferral.objects.exists()


def test_a_poisoned_expired_claim_no_longer_starves_the_claim_behind_it(monkeypatch: pytest.MonkeyPatch):
    capability = replace(primitive, policy=replace(primitive.policy, batch_size=1))
    first, behind = record(), record()
    for work, expired_for in ((first, 20), (behind, 10)):
        assert claim_work(PrimitiveWork, work.pk, lease_duration=primitive.policy.lease_duration) is not None
        PrimitiveWork.objects.filter(pk=work.pk).update(
            lease_expires_at=database_now() - timedelta(seconds=expired_for)
        )
    poisoned = {first.pk}
    original = PrimitiveEffect._finalize_retry

    def finalize_retry(self, work, error):
        if work.pk in poisoned:
            raise RuntimeError("retry finalizer cannot publish this claim")
        original(self, work, error)

    monkeypatch.setattr(PrimitiveEffect, "_finalize_retry", finalize_retry)
    with pytest.raises(ExceptionGroup):
        capability.recover()
    behind.refresh_from_db()
    assert behind.state == DurableWorkState.CLAIMED, "batch size 1: the first tick only saw the poison"

    capability.recover()
    behind.refresh_from_db()
    first.refresh_from_db()
    assert behind.state in REAPED, "the claim behind the poisoned one was reaped"
    assert first.state == DurableWorkState.CLAIMED, "the poisoned claim stays expired and owed"

    poisoned.clear()
    _end_the_hold(RecoveryPhase.REAP, first.pk)
    capability.recover()
    first.refresh_from_db()
    assert first.state in REAPED
    assert not RecoveryDeferral.objects.exists()


def test_an_identity_that_fails_for_weeks_keeps_its_capped_hold():
    """
    A poisoned identity is retried about once an hour for as long as it stays owed.

    Its failure count passes 41 in about a day and a half, and a backoff computed
    by doubling first overflows ``timedelta`` there: the hold could no longer be
    refreshed, and the identity would be retried on every tick from then on.
    """
    assert deferral_backoff(42) == DEFERRAL_CAP
    assert deferral_backoff(10_000) == DEFERRAL_CAP
    defer_identity(EFFECT, RecoveryPhase.REAP, "7", "RuntimeError('poisoned')")
    RecoveryDeferral.objects.filter(identity="7").update(failures=24 * 14)
    defer_identity(EFFECT, RecoveryPhase.REAP, "7", "RuntimeError('still poisoned')")
    hold = RecoveryDeferral.objects.get()
    assert hold.failures == 24 * 14 + 1
    assert DEFERRAL_CAP - timedelta(minutes=1) < hold.retry_at - database_now() <= DEFERRAL_CAP


def test_backoff_doubles_per_consecutive_failure_up_to_the_cap():
    assert [deferral_backoff(n) for n in (1, 2, 3)] == [
        timedelta(minutes=1),
        timedelta(minutes=2),
        timedelta(minutes=4),
    ]
    assert deferral_backoff(30) == DEFERRAL_CAP
    for _ in range(3):
        defer_identity(EFFECT, RecoveryPhase.REAP, "7", "RuntimeError('still failing')")
    hold = RecoveryDeferral.objects.get()
    assert hold.failures == 3
    assert timedelta(minutes=3) < hold.retry_at - database_now() <= timedelta(minutes=4)
    assert hold.first_failed_at <= hold.last_failed_at


def test_census_forgets_holds_no_page_selects_and_publishes_the_rest():
    defer_identity(EFFECT, RecoveryPhase.DISCOVERY, "1", "ValidationError()")
    defer_identity(EFFECT, RecoveryPhase.REAP, "2", "RuntimeError()")
    # Its hold ended long ago and it never failed again: no page selects it any more.
    RecoveryDeferral.objects.filter(identity="2").update(last_failed_at=database_now() - DEFERRAL_FORGET_AFTER * 2)
    # Its hold ended too, but it failed recently: a live identity waiting for its turn.
    RecoveryDeferral.objects.filter(identity="1").update(retry_at=database_now() - DEFERRAL_CAP * 3)
    published: list[tuple[str, int]] = []

    def capture(effect: str, phase: RecoveryPhase, *, count: int, oldest_age_seconds: float) -> None:
        published.append((phase.value, count))

    from unittest.mock import patch

    with patch("due.django.runtime.recovery_pass.record_recovery_deferrals", capture):
        publish_recovery_deferrals(EFFECT)
    assert list(RecoveryDeferral.objects.values_list("identity", flat=True)) == ["1"]
    assert ("discovery", 1) in published and ("reap", 0) in published


def _hold(identity: int, *, ended_ago: timedelta) -> None:
    # ARRANGE: a discovery hold whose backoff ended `ended_ago` (negative: still waiting).
    defer_identity(EFFECT, RecoveryPhase.DISCOVERY, str(identity), "ValidationError()")
    RecoveryDeferral.objects.filter(effect=EFFECT, identity=str(identity)).update(retry_at=database_now() - ended_ago)


def _owner_ids(owners: list[int]):
    return PrimitiveOwner.objects.filter(pk__in=owners).order_by("pk").values_list("pk", flat=True)


def test_a_page_serves_new_identities_first_and_retries_ended_holds_in_a_reserved_share():
    owners = [PrimitiveOwner.objects.create().pk for _ in range(30)]
    held_now, ended = owners[:5], owners[5:15]
    for identity in held_now:
        _hold(identity, ended_ago=-timedelta(minutes=10))
    for minutes, identity in enumerate(ended):
        # The hold that ended first is the last of them in selection order.
        _hold(identity, ended_ago=timedelta(minutes=minutes))

    page = RecoveryPass(EFFECT).page(RecoveryPhase.DISCOVERY, _owner_ids(owners), 10)

    assert not set(page) & set(held_now), "an identity still on hold was served"
    assert set(page) & set(ended) == {ended[-1]}, "one reserved retry, the hold that ended first"
    assert [identity for identity in page if identity != ended[-1]] == owners[15:24], "new identities fill the rest"


def test_ended_holds_fill_the_slots_new_identities_leave():
    owners = [PrimitiveOwner.objects.create().pk for _ in range(3)]
    for identity in owners[:2]:
        _hold(identity, ended_ago=timedelta(minutes=1))
    assert RecoveryPass(EFFECT).page(RecoveryPhase.DISCOVERY, _owner_ids(owners), 10) == owners


def test_many_poisoned_owners_cannot_starve_a_new_one(monkeypatch: pytest.MonkeyPatch):
    # Holds end faster than a page can serve the poisoned owners: by selection order alone,
    # they would fill every page and the healthy owner behind them would never be reached.
    monkeypatch.setattr(recovery_pass, "deferral_backoff", lambda failures: timedelta(seconds=1))
    poisoned = [PrimitiveOwner.objects.create() for _ in range(4)]
    healthy = PrimitiveOwner.objects.create()
    capability = _one_owner_per_page([*poisoned, healthy], {owner.pk for owner in poisoned}, page_size=2)
    for _ in range(4):
        with pytest.raises(RecoveryIncomplete):
            capability.recover()
        end_recovery_holds(PrimitiveWork)
    assert PrimitiveWork.objects.filter(owner=healthy, payload="derived").exists()


def test_a_query_that_repeats_an_identity_still_serves_it_once_and_fails_the_tick_loudly():
    first, second = PrimitiveOwner.objects.create(), PrimitiveOwner.objects.create()

    def candidates():
        # UNION ALL stands in for a to-many join without distinct.
        both = PrimitiveOwner.objects.filter(pk__in=[first.pk, second.pk])
        return both.union(PrimitiveOwner.objects.filter(pk=first.pk), all=True).order_by("id")

    capability = _one_owner_per_page([], set(), page_size=10, candidates=candidates)
    with pytest.raises(RecoveryIncomplete) as raised:
        capability.recover()
    assert raised.group_contains(ValueError, match="more than once")
    assert PrimitiveWork.objects.filter(owner__in=[first, second]).count() == 2


def test_a_soft_limit_while_recording_a_hold_ends_the_tick(monkeypatch: pytest.MonkeyPatch):
    owners = [PrimitiveOwner.objects.create() for _ in range(3)]
    capability = _one_owner_per_page(owners, {owner.pk for owner in owners}, page_size=10)
    attempted: list[str] = []

    def stopped_while_deferring(
        effect: str, phase: RecoveryPhase, identity: str, error: str, *, step: str = ""
    ) -> None:
        attempted.append(identity)
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(recovery_pass, "defer_identity", stopped_while_deferring)
    with pytest.raises(SoftTimeLimitExceeded):
        capability.recover()
    assert attempted == [str(owners[0].pk)], "the tick moved on after Celery asked it to stop"


def test_overlapping_ticks_both_count_the_first_failure_of_an_identity(monkeypatch: pytest.MonkeyPatch):
    barrier = threading.Barrier(2)
    clock = recovery_pass._coordination_now

    def both_before_either_inserts() -> datetime:
        barrier.wait(timeout=5)
        return clock()

    monkeypatch.setattr(recovery_pass, "_coordination_now", both_before_either_inserts)

    def tick() -> None:
        try:
            defer_identity(EFFECT, RecoveryPhase.REAP, "7", "RuntimeError('poison')")
        finally:
            connection.close()

    threads = [threading.Thread(target=tick) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert RecoveryDeferral.objects.get().failures == 2


def test_releasing_a_hold_that_fails_costs_only_that_identity(monkeypatch: pytest.MonkeyPatch):
    held, behind = PrimitiveOwner.objects.create(), PrimitiveOwner.objects.create()
    _hold(held.pk, ended_ago=timedelta(minutes=1))
    tick = RecoveryPass(EFFECT)
    assert tick.page(RecoveryPhase.DISCOVERY, _owner_ids([held.pk, behind.pk]), 2) == [held.pk, behind.pk]

    def lock_timeout(self: QuerySet, *args: object, **kwargs: object) -> None:
        raise OperationalError("canceling statement due to lock timeout")

    monkeypatch.setattr(QuerySet, "delete", lock_timeout)
    reached: list[int] = []
    for identity in (held.pk, behind.pk):
        with tick.isolate(RecoveryPhase.DISCOVERY, identity):
            reached.append(identity)
    assert reached == [held.pk, behind.pk] and len(tick.errors) == 1


def test_a_failure_that_breaks_the_enclosing_transaction_ends_the_tick():
    tick = RecoveryPass(EFFECT)
    with pytest.raises(DatabaseError), transaction.atomic():
        for identity in (1, 2, 3):
            # No savepoint of its own: after the error nothing else can run in this transaction.
            with tick.isolate(RecoveryPhase.DISPATCH, identity), connection.cursor() as cursor:
                cursor.execute("SELECT 1/0" if identity == 1 else "SELECT 1")
    assert not tick.errors


def test_a_failure_inside_its_own_savepoint_costs_only_its_identity():
    tick = RecoveryPass(EFFECT)
    with transaction.atomic():
        for identity in (1, 2, 3):
            with tick.isolate(RecoveryPhase.DISPATCH, identity), transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SELECT 1/0" if identity == 1 else "SELECT 1")
    assert len(tick.errors) == 1


def test_an_idle_tick_reads_its_holds_once():
    capability = _one_owner_per_page([], set())
    with CaptureQueriesContext(connection) as queries:
        capability.recover()
    holds = [query["sql"] for query in queries.captured_queries if RecoveryDeferral._meta.db_table in query["sql"]]
    # Discovery and reap share one read; the census is the other.
    assert len(holds) == 2, holds


def test_a_discovery_owner_must_have_a_primary_key_a_hold_can_store():
    assert deferral_identity_problem(PrimitiveOwner._meta.pk) is None
    assert deferral_identity_problem(models.UUIDField(primary_key=True)) is None
    assert deferral_identity_problem(models.CharField(max_length=64, primary_key=True)) is None
    assert "does not fit" in (deferral_identity_problem(models.CharField(max_length=65, primary_key=True)) or "")
    assert "does not fit" in (deferral_identity_problem(models.TextField(primary_key=True)) or "")


def test_held_names_identities_still_held_for_a_loop_that_selects_one_at_a_time():
    waiting, ended, gone = (PrimitiveOwner.objects.create() for _ in range(3))
    _hold(waiting.pk, ended_ago=-timedelta(minutes=30))
    _hold(ended.pk, ended_ago=timedelta(minutes=1))
    _hold(gone.pk, ended_ago=-timedelta(minutes=30))
    recovery = RecoveryPass(EFFECT)

    held = recovery.held(RecoveryPhase.DISCOVERY, _owner_ids([waiting.pk, ended.pk]))

    # An ended hold is served in its normal order; one the selection no longer returns is released.
    assert held == {str(waiting.pk)}
    assert set(RecoveryDeferral.objects.values_list("identity", flat=True)) == {str(waiting.pk), str(ended.pk)}
    # Selecting through held() makes the phase defer: the ended identity, failing again, is held again.
    with recovery.isolate(RecoveryPhase.DISCOVERY, ended.pk):
        raise ValueError("still poisoned")
    assert RecoveryDeferral.objects.get(identity=str(ended.pk)).retry_at > database_now()
    # And one that succeeds is released.
    with recovery.isolate(RecoveryPhase.DISCOVERY, waiting.pk):
        pass
    assert not RecoveryDeferral.objects.filter(identity=str(waiting.pk)).exists()


def test_held_refuses_a_phase_that_does_not_defer():
    with pytest.raises(ValueError, match="DEFERRING_PHASES"):
        RecoveryPass(EFFECT).held(RecoveryPhase.RETENTION, _owner_ids([]))


def test_a_hold_on_an_identity_the_query_no_longer_returns_is_released():
    # A fix that excludes the poisoned owner (as #5297 did for file-less images) heals it without
    # ever serving it again; its hold must not keep the deferred-identity alert firing.
    excluded, kept = PrimitiveOwner.objects.create(), PrimitiveOwner.objects.create()
    for owner in (excluded, kept):
        _hold(owner.pk, ended_ago=-timedelta(minutes=30))  # still held: neither is served this tick
    RecoveryPass(EFFECT).page(RecoveryPhase.DISCOVERY, _owner_ids([kept.pk]), 1)
    assert list(RecoveryDeferral.objects.values_list("identity", flat=True)) == [str(kept.pk)]


def _minute_by_minute_ticks(
    monkeypatch: pytest.MonkeyPatch,
    capability: PrimitiveEffect,
    *,
    minutes: int,
    before_tick: Callable[[int], None] = lambda minute: None,
) -> tuple[list[int], list[tuple[int, int, float]]]:
    """
    Run one real tick per simulated minute; return the minutes whose discovery isolated a failure, and per
    minute the census's published discovery hold (count, oldest age).

    Only the library's coordination clock is simulated: it is the one clock holds and their ages read.
    """
    origin = database_now()
    clock = {"now": origin}
    monkeypatch.setattr(recovery_pass, "_coordination_now", lambda: clock["now"])
    failed: list[int] = []
    published: list[tuple[int, int, float]] = []

    def count_failure(effect: str, phase: RecoveryPhase) -> None:
        if phase == RecoveryPhase.DISCOVERY:
            failed.append(minute)

    def publish(effect: str, phase: RecoveryPhase, *, count: int, oldest_age_seconds: float) -> None:
        if phase == RecoveryPhase.DISCOVERY:
            published.append((minute, count, oldest_age_seconds))

    monkeypatch.setattr(recovery_pass, "record_recovery_failure", count_failure)
    monkeypatch.setattr(recovery_pass, "record_recovery_deferrals", publish)
    for minute in range(minutes):
        clock["now"] = origin + timedelta(minutes=minute)
        before_tick(minute)
        try:
            capability.recover()
        except RecoveryIncomplete:
            assert failed and failed[-1] == minute, "a failed tick isolated a discovery failure this minute"
    return failed, published


def test_one_poisoned_owner_fails_hourly_while_its_published_hold_ages_past_an_hour(monkeypatch: pytest.MonkeyPatch):
    """
    The two facts the recovery alerts are built on (observability.yaml, durable-work-recovery-*).

    Backoff spaces a poisoned identity's failures out to one an hour, with clean ticks in between, so no
    rule over a short window of failures or of clean ticks can see it; the failing rule counts separate
    windows over two hours instead. Its hold, published by every census, keeps the age of its first
    failure, which is what durable-work-recovery-deferred pages on after an hour.
    """
    poisoned_owner, healthy = PrimitiveOwner.objects.create(), PrimitiveOwner.objects.create()
    capability = _one_owner_per_page([poisoned_owner, healthy], {poisoned_owner.pk}, page_size=10)

    failed, published = _minute_by_minute_ticks(monkeypatch, capability, minutes=150)

    # Holds of 1, 2, 4, ... minutes up to the 1-hour cap, ending exactly on a tick here. In production the
    # failure is recorded a little after the tick's clock, so each retry lands one tick later (0, 2, 5, 10, ...).
    assert failed == [0, 1, 3, 7, 15, 31, 63, 123], failed
    assert all(count == 1 for _minute, count, _age in published), "one owner is held for the whole run"
    ages = [age for _minute, _count, age in published]
    assert ages == sorted(ages), "the published age never resets while the owner keeps failing"
    assert next(minute for minute, _count, age in published if age > 3600) == 61, "an hour old at minute 61"


def test_a_healed_owner_publishes_no_hold_once_its_retry_succeeds(monkeypatch: pytest.MonkeyPatch):
    owner = PrimitiveOwner.objects.create()
    poisoned = {owner.pk}
    capability = _one_owner_per_page([owner], poisoned)

    def heal(minute: int) -> None:
        if minute == 1:
            poisoned.clear()

    failed, published = _minute_by_minute_ticks(monkeypatch, capability, minutes=4, before_tick=heal)

    assert failed == [0], "one transient failure"
    assert [(minute, count) for minute, count, _age in published] == [(0, 1), (1, 0), (2, 0), (3, 0)]
    assert all(age < 60 for _minute, _count, age in published), "a healed failure never ages toward the alert"


def test_an_owner_a_fix_excludes_publishes_no_hold_from_the_next_tick(monkeypatch: pytest.MonkeyPatch):
    """
    durable-work-recovery-deferred clears once a fix stops selecting the held identity.

    The fix (as #5297 did for file-less images) excludes the owner from the candidate query, so it is never
    served again and never succeeds; the page releases its hold because the query no longer returns it.
    """
    poisoned_owner, healthy = PrimitiveOwner.objects.create(), PrimitiveOwner.objects.create()
    selected = {poisoned_owner.pk, healthy.pk}
    capability = _one_owner_per_page(
        [],
        {poisoned_owner.pk},
        page_size=10,
        candidates=lambda: PrimitiveOwner.objects.filter(pk__in=selected).order_by("pk"),
    )

    def deploy_the_fix(minute: int) -> None:
        if minute == 62:
            selected.discard(poisoned_owner.pk)

    failed, published = _minute_by_minute_ticks(monkeypatch, capability, minutes=64, before_tick=deploy_the_fix)

    assert failed[-1] < 62
    assert [(count, age > 3600) for minute, count, age in published if minute == 61] == [(1, True)], "paging"
    assert [(minute, count, age) for minute, count, age in published if minute >= 62] == [(62, 0, 0.0), (63, 0, 0.0)]


@pytest.mark.parametrize("overlapping", [False, True])
def test_same_phase_steps_keep_independent_holds_and_retry(overlapping):
    from due.django.runtime.recovery_pass import RecoveryStep

    first, second = PrimitiveOwner.objects.create(), PrimitiveOwner.objects.create()
    second_id = first.pk if overlapping else second.pk
    seen = []
    poisoned = {first.pk}

    def fail(identity):
        if identity in poisoned:
            raise ValueError("cleanup refused")
        seen.append(("first", identity))
        return 1

    def succeed(identity):
        seen.append(("second", identity))
        return 1

    steps = (
        RecoveryStep.paged(
            RecoveryPhase.DISCOVERY,
            lambda: PrimitiveOwner.objects.filter(pk=first.pk).values_list("pk", flat=True),
            fail,
            size=1,
            key="first",
        ),
        RecoveryStep.paged(
            RecoveryPhase.DISCOVERY,
            lambda: PrimitiveOwner.objects.filter(pk=second_id).values_list("pk", flat=True),
            succeed,
            size=1,
            key="second",
        ),
    )
    for _ in range(2):
        tick = RecoveryPass(EFFECT)
        primitive._run_steps(steps, tick)
        if tick.errors:
            with pytest.raises(RecoveryIncomplete):
                tick.raise_if_failed()
    assert seen == [("second", second_id), ("second", second_id)]
    assert RecoveryDeferral.objects.get(effect=EFFECT, step="first").identity == str(first.pk)
    poisoned.clear()
    end_recovery_holds(PrimitiveWork)
    tick = RecoveryPass(EFFECT)
    primitive._run_steps(steps, tick)
    tick.raise_if_failed()
    assert ("first", first.pk) in seen
    assert not RecoveryDeferral.objects.filter(effect=EFFECT).exists()


def test_duplicate_step_keys_fail_before_any_action():
    from due.django.runtime.recovery_pass import RecoveryStep

    def never(recovery):
        pytest.fail("duplicate keys must fail before executing either step")

    with pytest.raises(ValueError, match="key"):
        primitive._run_steps(
            (
                RecoveryStep(RecoveryPhase.DISCOVERY, never, key="cleanup"),
                RecoveryStep(RecoveryPhase.OBSERVATION, never, key="cleanup"),
            ),
            RecoveryPass(EFFECT),
        )
