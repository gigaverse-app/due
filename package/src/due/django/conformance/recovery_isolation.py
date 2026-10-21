"""
One poisoned identity must not stall recovery of anything else (work tables).

Every recovery loop runs application code per identity: discovery builds desired
work from a product row, the reaper runs the application's retry/unknown
finalizers, dispatch publishes through the broker. Selection is deterministic and
oldest-first, so an identity whose application step always raises leads every
tick. Unless the loop isolates it, it blocks the identities behind it in that
loop forever, and the exception aborts every later phase of the tick: one bad row
then stops the reap and dispatch of every recorded obligation of its kind. That is
the dev incident behind PR #5298 (one ``file=''`` picture per kind, 10.6k errors
over three days).

These histories are generated for every registered work-table capability, for
each paging phase it runs. Each poisons exactly one identity at a seam the
application owns, drives the registered capability through its registered
recovery task, and judges through the shared oracle
(:func:`~pytest_obligation.poison.assert_poison_is_isolated`):
the poison fired, the healthy identity progressed in the same tick, the tick
reported the injected failure, and the poisoned identity is still owed. On top:

- the later phases still ran (a separately owed obligation was published);
- the poisoned identity is deferred: held with backoff and left out of the next
  tick, which runs clean.

The ``control`` case runs the same history without the poison, so a green
poisoned case cannot come from a history that never progresses at all.

Coverage follows the executable steps, not phase labels. Unchanged library
callbacks keep their shared proofs; every custom callback requires an exact
RecoveryStepBinding. Its generated history poisons a full first page so it also
proves progress across ticks.
"""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from pytest_obligation.poison import PoisonSwitch, assert_poison_is_isolated

from due.django.capabilities.authoring import _DurableWorkTransaction
from due.django.conformance.binding import entered_binding as _binding
from due.django.conformance.bindings import DurableWorkBindings
from due.django.conformance.recovery_steps import PHASE_PROOFS as PHASE_PROOFS
from due.django.conformance.recovery_steps import Generated as Generated
from due.django.conformance.recovery_steps import NoApplicationCode as NoApplicationCode
from due.django.conformance.recovery_steps import ProvenOnce as ProvenOnce
from due.django.conformance.recovery_steps import recovery_histories
from due.django.models import RecoveryDeferral
from due.django.public import (
    DurableWorkCapability,
    DurableWorkState,
    UniverseDurableWork,
    database_now,
)
from due.django.runtime.coordination import outstanding_work
from due.django.runtime.metrics import RecoveryPhase
from due.django.runtime.tasks import recover_durable_work_task
from due.django.testing import hold_nudges

_APPLY_ASYNC = "due.django.runtime.tasks.run_durable_work_task.apply_async"


def recovery_isolation_histories(capability: DurableWorkCapability[Any, Any]) -> tuple[str, ...]:
    """Derive histories from executable steps, including custom callbacks."""
    return recovery_histories(capability)


@dataclass(frozen=True)
class _History:
    """One poisoned-identity history over one paging phase, arranged before the tick."""

    phase: RecoveryPhase
    bad: Any
    #: Arms the application seam for ``bad`` and yields ``fired()``.
    poison: Callable[[], AbstractContextManager[Callable[[], int]]]
    progressed: Callable[[], bool]
    #: The poisoned identity is still owed; in the control, it progressed too.
    still_owed: Callable[[], bool]
    bad_progressed: Callable[[], bool]
    #: Whether the phase holds the identity in ``RecoveryDeferral`` (dispatch keeps a reservation instead).
    deferred: bool = True


def assert_one_poisoned_identity_does_not_stall_recovery(
    capability: DurableWorkCapability[Any, Any], examples: Any, *, history: str, poison: bool
) -> None:
    if history.startswith("step:"):
        from due.django.conformance.recovery_step_proof import assert_custom_recovery_step

        phase = RecoveryPhase(history.removeprefix("step:"))
        binding = examples.recovery_steps[phase]
        if binding.shared_history is None:
            assert_custom_recovery_step(capability, examples, phase, poison=poison)
            return
        history = binding.shared_history
    arrange = {"reap": _reap, "dispatch": _dispatch, "discovery": _discovery}[history]
    bindings = DurableWorkBindings(capability, examples.make_work)
    entry = bindings.registration
    published: list[int] = []
    refusals = PoisonSwitch()

    def broker(*, args: tuple[Any, ...], **options: object) -> None:
        if args[0] == entry.name:
            # EXTERNAL SEAM: the broker refuses an armed work id on every publication.
            refusals.check(args[1], f"broker refused work {args[1]}")
            published.append(args[1])

    def tick() -> None:
        recover_durable_work_task(entry.name)

    name = f"{entry.name}/{history}"
    with _binding(examples.environment), hold_nudges(), patch(_APPLY_ASYNC, broker), ExitStack() as stack:
        arranged = arrange(capability, examples, bindings, stack, refusals, published)
        owed = bindings.make(age=timedelta(minutes=10))  # Owed only to dispatch: proof the later phases ran.
        if poison:
            assert_poison_is_isolated(
                name=name,
                poison=arranged.poison(),
                run_tick=tick,
                progressed=arranged.progressed,
                still_owed=arranged.still_owed,
            )
            assert (
                not arranged.deferred
                or RecoveryDeferral.objects.filter(
                    effect=capability.model._meta.label_lower, phase=arranged.phase, identity=str(arranged.bad)
                ).exists()
            ), f"{name}: the isolated identity was not deferred"
            with arranged.poison():
                # Still poisoned, yet the next tick is clean: the identity no longer leads its page.
                tick()
        else:
            tick()
            assert arranged.progressed() and arranged.bad_progressed(), f"{name}: the control history did not progress"
        assert owed.pk in published, (
            f"{name}: dispatch did not run after the {'poisoned' if poison else 'control'} phase; one bad "
            f"identity stopped delivery of every owed obligation of its kind"
        )


def _claimed_and_expired[W: UniverseDurableWork](
    capability: DurableWorkCapability[W, Any], bindings: DurableWorkBindings[W, Any], *, expired_for: timedelta
) -> W:
    work = bindings.make()
    if work.state != DurableWorkState.CLAIMED:
        bindings.claim(work)
    # ARRANGE: a dead worker. Ordering by expiry puts the poisoned row first.
    capability.model.objects.filter(pk=work.pk).update(lease_expires_at=database_now() - expired_for)
    work.refresh_from_db()
    return work


@contextmanager
def _raising_for(
    owner: type, names: tuple[str, ...], switch: PoisonSwitch, key_of: Callable[..., Any]
) -> Iterator[None]:
    """Route the named application hooks through ``switch``: they raise for the armed identity only."""
    with ExitStack() as patches:
        for name in names:
            original = getattr(owner, name)

            def poisoned(self: Any, *args: Any, _original: Any = original, _name: str = name, **kwargs: Any) -> Any:
                switch.check(key_of(*args), f"{_name} for the poisoned identity")
                return _original(self, *args, **kwargs)

            patches.enter_context(patch.object(owner, name, poisoned))
        yield


def _reaped(work: Any) -> bool:
    work.refresh_from_db()
    return work.lease_expires_at is None or work.lease_expires_at > database_now()


def _reap(capability, examples, bindings, stack: ExitStack, refusals: PoisonSwitch, published: list[int]) -> _History:
    bad = _claimed_and_expired(capability, bindings, expired_for=timedelta(seconds=20))
    good = _claimed_and_expired(capability, bindings, expired_for=timedelta(seconds=10))
    switch = PoisonSwitch()
    hooks = ("_finalize_retry", "_finalize_unknown", "settle_expired_claim", "retry_delay_for")
    stack.enter_context(_raising_for(type(capability), hooks, switch, lambda work, *rest: getattr(work, "pk", None)))

    def still_expired_and_owed() -> bool:
        bad.refresh_from_db()
        return bad.state in (DurableWorkState.CLAIMED, DurableWorkState.QUEUED) and not _reaped(bad)

    return _History(
        phase=RecoveryPhase.REAP,
        bad=bad.pk,
        poison=lambda: switch.poisoned(bad.pk),
        progressed=lambda: _reaped(good),
        still_owed=still_expired_and_owed,
        bad_progressed=lambda: _reaped(bad),
    )


def _dispatch(
    capability, examples, bindings, stack: ExitStack, refusals: PoisonSwitch, published: list[int]
) -> _History:
    # Dispatch defers through its retained reservation, not a deferral row.
    bad = bindings.make(age=timedelta(minutes=20))
    good = bindings.make(age=timedelta(minutes=10))

    def reservation_is_recoverable() -> bool:
        # Still owed in a state recovery serves, and not parked behind a lease the reaper cannot reach.
        bad.refresh_from_db()
        reachable = bad.lease_expires_at is None or bad.lease_expires_at <= database_now() + (
            capability.policy.lease_duration * 2
        )
        return bad.state in outstanding_states() and reachable

    return _History(
        phase=RecoveryPhase.DISPATCH,
        bad=bad.pk,
        poison=lambda: refusals.poisoned(bad.pk),
        progressed=lambda: good.pk in published,
        still_owed=reservation_is_recoverable,
        bad_progressed=lambda: bad.pk in published,
        deferred=False,
    )


def _discovery(
    capability, examples, bindings, stack: ExitStack, refusals: PoisonSwitch, published: list[int]
) -> _History:
    assert examples.derivation is not None, "registered discovery requires derivation examples"
    facts = stack.enter_context(_binding(examples.derivation))
    first, second = facts.make_implied(), facts.make_implied()
    page = list(capability.discovery.bounded_candidates(now=database_now()).values_list("pk", flat=True))
    assert {first, second} <= set(page), "positive control: both facts must be discovery candidates"
    bad, good = sorted((first, second), key=page.index)
    switch = PoisonSwitch()
    original = _DurableWorkTransaction.request_work

    def request_work(self: _DurableWorkTransaction, *args: Any, **kwargs: Any) -> Any:
        # The owner's fact cannot become valid work, e.g. desired input failing full_clean.
        switch.check(self.locked_owner.pk, f"desired work for owner {self.locked_owner.pk}")
        return original(self, *args, **kwargs)

    stack.enter_context(patch.object(_DurableWorkTransaction, "request_work", request_work))

    def enrolled(owner: Any) -> bool:
        return owner in set(outstanding_work(capability.model).values_list(capability.owner_attname, flat=True))

    def still_a_candidate() -> bool:
        # Still owed: no work was invented for it, and discovery still selects it.
        return not enrolled(bad) and capability.discovery.bounded_candidates(now=database_now()).filter(pk=bad).exists()

    return _History(
        phase=RecoveryPhase.DISCOVERY,
        bad=bad,
        poison=lambda: switch.poisoned(bad),
        progressed=lambda: enrolled(good),
        still_owed=still_a_candidate,
        bad_progressed=lambda: enrolled(bad),
    )


def outstanding_states() -> tuple[DurableWorkState, ...]:
    return (DurableWorkState.READY, DurableWorkState.RETRYABLE, DurableWorkState.QUEUED, DurableWorkState.CLAIMED)
