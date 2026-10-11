"""Application-bound UNKNOWN histories; production owns every state transition."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import pytest
from pytest_obligation.crash_histories import (
    ExternalCall,
    HandoffHistory,
)

# pytest-global: Shared generated assertions feed the existing optional product-observation report.
from pytest_obligation.evidence.observation import assert_observation

# pytest-global: Shared observer histories narrow the real capability/model contract; no application setup on import.
from due.django.capabilities.observed_operations import (
    ObservedExternalOperation,
    ObservedExternalOperationWork,
)
from due.django.conformance._binding import Binding, entered_binding

# pytest-global: All generated histories share the broker recorder; importing its types installs no fixture.
from due.django.conformance.background import (
    BackgroundWork,
    Delivery,
    accounted_background_work,
    frozen_scheduler_clock,
)

# pytest-global: All observer suites resolve their actual registration through the same canonical binding.
from due.django.conformance.bindings import DurableWorkBindings

# pytest-global: Shared histories inspect original production evidence independently of application observations.
from due.django.evidence.attempts import attempts_for

# pytest-global: Generated assertions use canonical runtime states for all observer adopters.
from due.django.models import DurableWorkState, EffectAttemptOutcome

# pytest-global: Generated observer histories bind registered production recovery and evidence APIs.
from due.django.public import DurableWorkCapability, UniverseDurableWork

# pytest-global: Cross-adopter assertions use the actual selection and time authority, never copied predicates.
from due.django.runtime.coordination import database_now, due_work, outstanding_work

# pytest-global: The generator must drive installed production tasks for every adopter; these are declarations only.
from due.django.runtime.tasks import observe_durable_work_task, recover_durable_work_task

# pytest-global: The shared history ages only time through the existing root control; production owns transitions.
from due.django.testing import hold_nudges, make_due, make_observation_due

# pytest-global: Every observer history requires the canonical uncertain worker outcome, not an application-specific exception.
from due.errors import EffectOutcomeUnknownError

if TYPE_CHECKING:
    # pytest-global: Type-only reference to the existing delivery example avoids a second product-observation shape.
    from due.django.conformance.suite import DeliveryExample


type ObservationHistory = Literal["inconclusive", "lost_delivery", "competing_receipt"]
OBSERVATION_HISTORIES: tuple[ObservationHistory, ...] = ("inconclusive", "lost_delivery", "competing_receipt")


# pytest-global: Mandatory observer enrollment needs this shared provider-control contract; no fixtures execute here.
@dataclass(frozen=True, kw_only=True)
class ObservationExample[W: UniverseDurableWork, ObservationT, ResultT]:
    """
    Pending real admission; the provider loses the create response, then reveals it.

    `reveal` changes provider inventory only and returns its canonical receipt.
    `delivery.expected` is the eventual product outcome; both counters observe
    provider calls independently of work state. No application recovery callbacks.
    """

    delivery: DeliveryExample[W, ObservationT]
    reveal: Callable[[], ResultT]
    observation_calls: Callable[[], int]


@dataclass(frozen=True)
class _ObservationHandoff:
    example: ObservationExample
    message: Delivery
    creates_before: int


def _reserve_observation(
    capability: ObservedExternalOperation, name: str, work: UniverseDurableWork, driver: BackgroundWork
) -> Delivery:
    # Name a clock cause before blaming reservation: the selector compares
    # next_observation_at with the coordination clock and misses silently.
    assert capability.observations_due().filter(pk=work.pk).exists(), (
        "uncertain work is not due for observation on the database clock; a timestamp from the "
        "real PostgreSQL clock is ahead of a frozen test clock that started before it"
    )
    driver.hold()
    before = len(driver.dispatched(observe_durable_work_task))
    recover_durable_work_task(name)
    recover_durable_work_task(name)
    sent = driver.dispatched(observe_durable_work_task)[before:]
    assert len(sent) == 1, "recovery must reserve exactly one observation across competing ticks"
    assert sent[0].args[:2] == (name, work.pk)
    return sent[0]


def observation_handoff(
    name: str,
    capability: ObservedExternalOperation,
    example: Binding[ObservationExample],
    *,
    external_calls: tuple[ExternalCall, ...] = (),
) -> HandoffHistory[_ObservationHandoff, dict[str, Any]]:
    """
    Reuse an observer example for every shared handoff interruption history.

    The adopter supplies its existing admission, provider reveal, product outcome
    and counters. This adapter owns UNKNOWN execution and reservation through the
    registered recovery task; no adopter touches tokens or private dispatch hooks.
    Each history gets a fresh example scope, kept alive through its final read.
    """

    @contextmanager
    def arrange() -> Iterator[_ObservationHandoff]:
        with entered_binding(example) as scenario:
            delivery = scenario.delivery
            calls = delivery.effect_calls
            assert calls is not None, "observation needs an independent create-call counter"
            creates = calls()
            make_due(delivery.work)
            with hold_nudges(), pytest.raises(EffectOutcomeUnknownError):
                capability.attempt_execution(delivery.work.pk)
            assert calls() == creates + 1, "positive control: real admission must reach the provider once"
            # Reveal provider state, never write a receipt or settle work here:
            # the interrupted production observer must discover and publish it.
            scenario.reveal()
            registration = DurableWorkBindings(capability, lambda: delivery.work).registration.name
            with (
                frozen_scheduler_clock() as clock,
                pytest.MonkeyPatch.context() as patcher,
                accounted_background_work(patcher, clock) as driver,
            ):
                message = _reserve_observation(capability, registration, delivery.work, driver)
            yield _ObservationHandoff(scenario, message, creates)

    def observe(handle: _ObservationHandoff) -> dict[str, Any]:
        delivery = handle.example.delivery
        assert delivery.effect_calls is not None
        return {
            "product": delivery.observe() == delivery.expected,
            "create_requests": delivery.effect_calls() - handle.creates_before,
        }

    return HandoffHistory(
        name=name,
        arrange=arrange,
        transition=lambda handle: capability.attempt_observation(*handle.message.args[1:]),
        observe=observe,
        external_calls=external_calls,
    )


def assert_observation_recovers_without_resend[
    W: UniverseDurableWork,
    ObservationT,
    RunT,
    ResultT,
](
    capability: DurableWorkCapability[W, RunT],
    example: ObservationExample[W, ObservationT, ResultT],
    driver: BackgroundWork,
    *,
    history: ObservationHistory,
) -> None:
    assert isinstance(capability, ObservedExternalOperation)
    delivery = example.delivery
    work = delivery.work
    assert isinstance(work, ObservedExternalOperationWork)
    calls = delivery.effect_calls
    assert calls is not None, "observation needs an independent create-call counter"
    assert delivery.observe() != delivery.expected, "observation must change the visible product outcome"
    name = DurableWorkBindings(capability, lambda: work).registration.name
    creates, reads = calls(), example.observation_calls()
    revision, rearms = work.desired_revision, work.rearm_count
    make_due(work)
    make_observation_due(work)
    with pytest.raises(EffectOutcomeUnknownError):
        driver.run_recovery(name)
    work.refresh_from_db()
    assert work.state == DurableWorkState.UNKNOWN
    attempt = attempts_for(work).get()
    assert attempt.outcome == EffectAttemptOutcome.UNKNOWN
    assert calls() == creates + 1, "positive control: real admission must reach the provider once"

    pending = _reserve_observation(capability, name, work, driver)
    assert example.observation_calls() == reads, "recovery tick must perform no provider observation"
    previous_reads = reads
    if history == "inconclusive":
        driver.release()
        work.refresh_from_db()
        attempt.refresh_from_db()
        previous_reads = example.observation_calls()
        assert previous_reads > reads, "inconclusive evidence must come from the provider"
        assert work.state == DurableWorkState.UNKNOWN
        assert attempt.outcome == EffectAttemptOutcome.UNKNOWN and attempt.repair_pending
        assert work.observation_claimed_at is None
        assert work.next_observation_at > database_now(), "inconclusive observations must be paced"
        assert delivery.observe() != delivery.expected, "empty inventory cannot announce success"
        assert outstanding_work(capability.model).filter(pk=work.pk).exists()
        assert not due_work(capability.model).filter(pk=work.pk).exists()
        driver.run_recovery(name)
        assert example.observation_calls() == previous_reads, "another tick must respect observation pacing"
        example.reveal()
        driver.advance(capability.observation_interval.total_seconds() + 1)
        pending = _reserve_observation(capability, name, work, driver)
        assert example.observation_calls() == previous_reads, "recovery tick must perform no provider observation"
    elif history == "lost_delivery":
        assert driver.drop_held(observe_durable_work_task) == [pending]
        example.reveal()
        driver.advance(capability.policy.lease_duration.total_seconds() + 1)
        replacement = _reserve_observation(capability, name, work, driver)
        assert replacement.args != pending.args, "expired delivery needs a fresh reservation"
        assert not observe_durable_work_task(*pending.args), "lost delivery token must be fenced"
        assert example.observation_calls() == reads, "stale delivery must not reach the provider"
        pending = replacement
    else:
        receipt = example.reveal()
        assert capability.record_observed_effect_evidence(work.pk, receipt)
        assert capability.settle_from_applied_effect_evidence(work.pk)
    driver.release()
    completed_reads = example.observation_calls()
    if history == "competing_receipt":
        assert completed_reads == reads, "settled work must skip provider observation"
    else:
        # One observation may read several pages or resources. Require actual
        # I/O here, and no additional I/O when stale/duplicate/paced elsewhere.
        assert completed_reads > previous_reads, "positive evidence must come from the provider"
    assert not observe_durable_work_task(*pending.args), "consumed observation token cannot run twice"
    work.refresh_from_db()
    attempt.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED, "observation must settle the original obligation"
    assert work.observation_claimed_at is None
    assert attempt.outcome == EffectAttemptOutcome.APPLIED and not attempt.repair_pending
    assert list(attempts_for(work).values_list("pk", flat=True)) == [attempt.pk]
    assert (work.desired_revision, work.rearm_count) == (revision, rearms)
    assert calls() == creates + 1, "observation must never create a replacement occurrence"
    assert example.observation_calls() == completed_reads, "duplicate delivery must not reach the provider"
    assert_observation(
        delivery.observe(), delivery.expected, because="observation recovers the required product outcome"
    )
