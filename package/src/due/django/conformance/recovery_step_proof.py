"""Exercise custom recovery through the registered tick with a poisoned first page."""

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from typing import Any
from unittest.mock import patch

from pytest_obligation.poison import assert_poison_is_isolated

from due.django.capabilities.authoring import DurableWorkCapability
from due.django.conformance.binding import entered_binding
from due.django.conformance.bindings import DurableWorkBindings
from due.django.conformance.examples import EffectExamples
from due.django.conformance.recovery_steps import (
    RecoveryIdentity,
    RecoveryStepExample,
    poison_paged_step,
    validate_recovery_step_bindings,
)
from due.django.models import RecoveryDeferral
from due.django.runtime.metrics import RecoveryPhase
from due.django.runtime.recovery_pass import RecoveryIncomplete
from due.django.runtime.tasks import recover_durable_work_task, run_durable_work_task
from due.django.testing import end_recovery_holds, hold_nudges

type _Poison = Callable[[Sequence[RecoveryIdentity]], AbstractContextManager[Callable[[], int]]]


def _poison_for(facts: RecoveryStepExample, phase: RecoveryPhase) -> _Poison:
    if facts.poison is not None:
        return facts.poison
    return lambda identities: poison_paged_step(phase, identities)


def assert_custom_recovery_step(
    capability: DurableWorkCapability[Any, Any], examples: EffectExamples, phase: RecoveryPhase, *, poison: bool
) -> None:
    validate_recovery_step_bindings(capability, examples.recovery_steps)
    binding = examples.recovery_steps[phase]
    entry = DurableWorkBindings(capability, examples.make_work).registration
    with (
        entered_binding(examples.environment),
        hold_nudges(),
        patch.object(run_durable_work_task, "apply_async"),
        entered_binding(binding.example) as facts,
    ):
        # A single bad row in a large page tests isolation, not fairness. Fill
        # the real configured page, then put healthy work just beyond it.
        candidates = [facts.make_pending() for _ in range(capability.policy.batch_size + 1)]
        assert len(set(candidates)) == len(candidates), "recovery examples must create independent candidates"
        assert all(facts.pending(identity) for identity in candidates), "every arranged candidate must owe work"
        bad, healthy = candidates[:-1], candidates[-1]
        poisoned = _poison_for(facts, phase)

        def tick() -> None:
            recover_durable_work_task(entry.name)

        def two_ticks() -> None:
            failures = []
            for _ in range(2):
                try:
                    tick()
                except RecoveryIncomplete as error:
                    failures.append(error)
            if failures:
                raise ExceptionGroup("custom recovery fault histories", failures)

        if poison:
            try:
                assert_poison_is_isolated(
                    name=f"{entry.name}/{phase.value}",
                    poison=poisoned(bad),
                    run_tick=two_ticks,
                    progressed=lambda: not facts.pending(healthy),
                    still_owed=lambda: all(facts.pending(identity) for identity in bad),
                )
            except AssertionError as error:
                if facts.poison is None and "never fired" in str(error):
                    error.add_note(
                        f"The default fault is raised at RecoveryPass.run_page's per-row step for {phase.value}. "
                        "Page this step through run_page, or supply RecoveryStepExample(poison=...) at the per-row "
                        "seam it actually calls."
                    )
                raise
            assert set(
                RecoveryDeferral.objects.filter(effect=capability.work_name, phase=phase).values_list(
                    "identity", flat=True
                )
            ) >= {str(identity) for identity in bad}, "every failed candidate must remain deferred for a later retry"
            # A still-broken page is held aside: no fault should fire next tick.
            with poisoned(bad) as fired:
                tick()
                assert fired() == 0, "deferred candidates were retried before their hold ended"
            # Backoff cannot become abandonment: the same production loop must
            # finish the preserved obligations after the fault and hold end.
            end_recovery_holds(capability.model)
            two_ticks()
            assert not any(facts.pending(identity) for identity in bad), "deferred recovery work was abandoned"
        else:
            two_ticks()
            assert not any(facts.pending(identity) for identity in candidates), (
                "healthy recovery control did not progress"
            )
