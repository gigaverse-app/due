"""Bind Due's task, page action and deferral clock to obligation's shared history."""

from typing import Any
from unittest.mock import patch

from pytest_obligation.recovery import assert_recovery_history

from due.django.capabilities.authoring import DurableWorkCapability
from due.django.conformance.binding import entered_binding
from due.django.conformance.bindings import DurableWorkBindings
from due.django.conformance.examples import EffectExamples
from due.django.conformance.recovery_steps import bound_recovery_steps, poison_paged_step
from due.django.models import RecoveryDeferral
from due.django.runtime.metrics import RecoveryPhase
from due.django.runtime.recovery_pass import RecoveryPage
from due.django.runtime.tasks import recover_durable_work_task, run_durable_work_task
from due.django.testing import end_recovery_holds, hold_nudges


def assert_custom_recovery_step(
    capability: DurableWorkCapability[Any, Any], examples: EffectExamples, phase: RecoveryPhase, *, poison: bool
) -> None:
    binding = bound_recovery_steps(capability, examples.recovery_steps)[phase]
    entry = DurableWorkBindings(capability, examples.make_work).registration
    with (
        entered_binding(examples.environment),
        hold_nudges(),
        patch.object(run_durable_work_task, "apply_async"),
        entered_binding(binding.example) as facts,
    ):
        run = binding.run
        default_fault = facts.poison is None
        if default_fault:
            # An explicit product fault may be inside a transaction; keep it.
            # Otherwise the actual pager supplies the per-row boundary.
            facts = facts.model_copy(
                update={"poison": lambda identities: poison_paged_step(phase, identities, effect=capability.work_name)}
            )

        def held(identities):
            actual = set(
                RecoveryDeferral.objects.filter(effect=capability.work_name, phase=phase).values_list(
                    "identity", flat=True
                )
            )
            assert actual >= {str(identity) for identity in identities}, (
                "every failed candidate must remain deferred for a later retry"
            )

        try:
            assert_recovery_history(
                name=f"{entry.name}/{phase.value}",
                example=facts,
                run_tick=lambda: recover_durable_work_task(entry.name),
                page_size=run.size if isinstance(run, RecoveryPage) else capability.policy.batch_size,
                poison=poison,
                resume=lambda: end_recovery_holds(capability.model),
                assert_held=held,
            )
        except AssertionError as error:
            if default_fault and "never fired" in str(error):
                error.add_note(
                    "Use RecoveryStep.paged or RecoveryPass.run_page for this loop, or supply "
                    "RecoveryStepExample(poison=...) at the per-row boundary it actually calls."
                )
            raise
