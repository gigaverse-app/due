"""Reusable crash schedules across a producer and its independent compensation."""

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from unittest.mock import patch

import pytest

from due.django.capabilities.compensation import IndependentCompensation
from due.django.capabilities.projection import DurableProjection
from due.django.capabilities.scheduled import CancellationResult, DurableScheduledAction
from due.django.conformance.bindings import assert_registered_worker_options
from due.django.conformance.registrations import registered_capability, registration_of
from due.django.evidence.attempts import attempts_for, observe_effect_outcome
from due.django.models import EffectAttemptOutcome
from due.django.public import (
    DurableWorkCapability,
    DurableWorkState,
    UniverseDurableWork,
    database_now,
)
from due.django.runtime.registry import installed
from due.django.runtime.tasks import (
    recover_durable_work_task,
    run_durable_work_task,
)
from due.django.testing import WorkerDied, expire_lease, hold_nudges


@dataclass(frozen=True, kw_only=True)
class CompensationExample[W: UniverseDurableWork, ObservationT]:
    """A producer and independently owed cleanup, observed at the external boundary.

    ``provider_path`` is the producer seam; ``produce_output(work)`` arranges late
    output. ``retire()`` calls the real retirement command and returns cleanup
    rows. ``observe()`` must reach ``clean`` even after producer exhaustion."""

    work: W
    provider_path: str
    produce_output: Callable[[W], object]
    retire: Callable[[], tuple[UniverseDurableWork, ...]]
    observe: Callable[[], ObservationT]
    clean: ObservationT


@dataclass(frozen=True, kw_only=True)
class SupersessionExample[W: UniverseDurableWork, ObservationT]:
    """Existing work, a real desired-input change, and an independent product observation."""

    work: W
    change: Callable[[], object]
    observe: Callable[[], ObservationT]


@dataclass(frozen=True, kw_only=True)
class ProjectionExample[W: UniverseDurableWork, ObservationT](SupersessionExample[W, ObservationT]):
    """Supersession whose eventual observable result must equal the latest target."""

    latest: ObservationT


@dataclass(frozen=True, kw_only=True)
class CancellationExample[W: UniverseDurableWork, ObservationT](SupersessionExample[W, ObservationT]):
    """Cancellation after provider entry; completed names the actual external outcome."""

    completed: ObservationT


def assert_cancellation_after_entry_is_honest(
    capability: DurableScheduledAction, example: CancellationExample, *, worker_dies: bool
) -> None:
    """Prove late cancellation reports admitted I/O honestly, including worker death."""
    original = capability.perform_effect
    entered = []

    def call(self, work):
        attempt = attempts_for(work).get(outcome=EffectAttemptOutcome.UNKNOWN)
        entered.append(attempt)
        assert example.change() == CancellationResult.ALREADY_STARTED, "cancellation must report admitted provider I/O"
        original(work)
        if worker_dies:
            raise WorkerDied()

    assert example.observe() != example.completed, "positive control: the scheduled action must still be owed"
    with patch.object(type(capability), "perform_effect", call):
        if worker_dies:
            with pytest.raises(WorkerDied):
                capability.attempt_execution(example.work.pk)
        else:
            assert capability.attempt_execution(example.work.pk)
    assert len(entered) == 1, "the real executor must enter the provider once"
    assert example.observe() == example.completed, "admitted provider work must reach the actual domain outcome"
    if worker_dies:
        entered[0].refresh_from_db()
        assert entered[0].outcome == EffectAttemptOutcome.UNKNOWN
        assert example.change() == CancellationResult.ALREADY_STARTED, "worker death cannot prove remote cancellation"
        recover_and_deliver(example.work)
        assert example.observe() == example.completed
    example.work.refresh_from_db()
    assert example.work.state == DurableWorkState.SUCCEEDED


def assert_late_projection_write_is_repaired(
    capability: DurableProjection, example: ProjectionExample, *, worker_dies: bool
) -> None:
    """Let a stale external write finish late, then require recovery to restore the latest target."""
    original = capability.write_projection
    entered = False
    escaped = []

    def write(self, work):
        nonlocal entered
        if entered:
            return original(work)
        entered = True
        escaped.append(attempts_for(work).get(outcome=EffectAttemptOutcome.UNKNOWN))
        # Hold the old call open, change product state and execute its successor.
        example.change()
        assert capability.attempt_execution(work.pk), "the successor must execute while the old call can still land"
        assert example.observe() == example.latest, "positive control: successor must publish the desired result"
        original(work)
        assert example.observe() != example.latest, "positive control: the old provider write must actually land late"
        if worker_dies:
            raise WorkerDied()

    with (
        hold_nudges(),
        patch("due.django.runtime.tasks.run_durable_work_task.apply_async"),
        patch.object(type(capability), "write_projection", write),
    ):
        if worker_dies:
            with pytest.raises(WorkerDied):
                capability.attempt_execution(example.work.pk)
        else:
            assert not capability.attempt_execution(example.work.pk), (
                "the old owner must not settle current product state"
            )
        old = escaped[0]
        old.refresh_from_db()
        assert old.repair_pending, "late or unknown work must retain repair debt after successor settlement"
        recover_and_deliver(example.work)
        assert example.observe() == example.latest, "recovery must restore the latest visible projection"
        if worker_dies:
            old.refresh_from_db()
            assert old.outcome == EffectAttemptOutcome.UNKNOWN and old.repair_pending
            # Definite late evidence is supplied through the production evidence seam.
            observe_effect_outcome(old, EffectAttemptOutcome.APPLIED)
            recover_and_deliver(example.work)
            assert example.observe() == example.latest
        old.refresh_from_db()
        assert not old.repair_pending, "definite old completion must be covered by a subsequent current write"


def compensations_for[W: UniverseDurableWork, ResultT](
    capability: DurableWorkCapability[W, ResultT],
) -> tuple[IndependentCompensation, ...]:
    """Return installed independent compensation capabilities for this producer model."""
    return tuple(
        cleanup
        for entry in installed.get().values()
        if isinstance(cleanup := registered_capability(entry.name), IndependentCompensation)
        and cleanup.producer_model is capability.model
    )


def recover_and_deliver(work: UniverseDurableWork) -> None:
    """Recover one work identity and execute the registered production worker delivery."""
    entry = registration_of(type(work))
    sent = []

    def capture(*, args, **options):
        if args[0] == entry.name and args[1] == work.pk:
            sent.append((args, options))

    # Advance eligibility/leases only. Reaping, retry and reservation remain production-owned.
    entry.model.objects.filter(pk=work.pk).update(available_at=database_now() - timedelta(seconds=1))
    entry.model.objects.filter(pk=work.pk, lease_expires_at__isnull=False).update(
        lease_expires_at=database_now() - timedelta(seconds=1)
    )
    with patch("due.django.runtime.tasks.run_durable_work_task.apply_async", capture):
        recover_durable_work_task(entry.name)
        work.refresh_from_db()
        if work.state == DurableWorkState.RETRYABLE:
            entry.model.objects.filter(pk=work.pk).update(available_at=database_now() - timedelta(seconds=1))
            recover_durable_work_task(entry.name)
    assert len(sent) == 1, "recovery must deliver the exact still-owed component"
    args, options = sent[0]
    assert_registered_worker_options(entry, options)
    assert run_durable_work_task(*args), "component delivery must reach its actual executor"


def assert_exhausted_producer_still_compensates[W: UniverseDurableWork, ResultT](
    primary: DurableWorkCapability[W, ResultT],
    cleanup: IndependentCompensation,
    example: CompensationExample,
) -> None:
    """Require independent cleanup after producer exhaustion, including a late output race."""
    assert type(example.work) is primary.model
    assert cleanup.producer_model is primary.model
    work = example.work
    captured = []
    original_capture = cleanup.capture

    def capture(self, producer):
        # Record the actual unsaved object; production saves and reserves it.
        actual = original_capture(producer)
        captured.append(actual)
        return actual

    def output_then_die(current):
        for retired in example.retire():
            recover_and_deliver(retired)
        assert example.observe() == example.clean, "retirement must precede the escaped output"
        assert captured and captured[-1].pk is not None, "cleanup must be committed before output"
        example.produce_output(current)
        raise WorkerDied()

    with (
        hold_nudges(),
        patch("due.django.runtime.tasks.run_durable_work_task.apply_async"),
        patch.object(type(cleanup), "capture", capture),
    ):
        # Spend the actual policy's retry budget through real failed provider calls.
        assert primary.policy.max_rearms is not None, "this scenario requires a bounded primary policy"
        for _ in range(primary.policy.max_rearms):
            with patch(example.provider_path, side_effect=OSError("provider unavailable")), suppress(OSError):
                primary.attempt_execution(work.pk)
            work.refresh_from_db()
            assert work.state == DurableWorkState.RETRYABLE
            primary.model.objects.filter(pk=work.pk).update(available_at=database_now() - timedelta(seconds=1))
        with patch(example.provider_path, output_then_die), pytest.raises(WorkerDied):
            primary.attempt_execution(work.pk)
        assert example.observe() != example.clean, "positive control: output escaped after retirement"
        expire_lease(work)
        primary.recover()
        work.refresh_from_db()
        assert work.state == DurableWorkState.EXHAUSTED
        compensation = captured[-1]

        # Compensation has its own failure budget even after primary exhaustion.
        with patch.object(type(cleanup), "compensate", side_effect=OSError("cleanup unavailable")):
            with pytest.raises(OSError):
                recover_and_deliver(compensation)
        compensation.refresh_from_db()
        assert compensation.state == DurableWorkState.RETRYABLE
        recover_and_deliver(compensation)
        assert example.observe() == example.clean, "independent cleanup must remove escaped output"

        # An already-admitted PUT can land after a successful DELETE.
        example.produce_output(work)
        assert example.observe() != example.clean
        recover_and_deliver(compensation)
        assert example.observe() == example.clean
        work.refresh_from_db()
        assert work.state == DurableWorkState.EXHAUSTED, "cleanup must not resurrect primary execution"
