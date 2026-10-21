"""Match executable recovery steps to library proofs or adopter fault bindings.

A phase is a metric/deferral namespace, not an implementation identity. Reusing
its name cannot lend a library proof to an application callback.
"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from types import MethodType
from typing import Any
from unittest.mock import patch
from uuid import UUID

from pytest_obligation.contract import ObligationContractDesignError
from pytest_obligation.poison import PoisonSwitch
from pytest_obligation.recovery import RecoveryExample as RecoveryStepExample
from pytest_obligation.recovery import poison_call, poison_many, validate_recovery_bindings

from due.django.capabilities._attempt_recovery import AttemptRecovery, attempt_recovery_steps
from due.django.capabilities.authoring import DurableWorkCapability
from due.django.capabilities.projection import ProjectionRecovery
from due.django.conformance.binding import Binding
from due.django.runtime.metrics import RecoveryPhase
from due.django.runtime.recovery_pass import RecoveryPage, RecoveryPass, RecoveryStep

type RecoveryIdentity = int | str | UUID


@dataclass(frozen=True)
class Generated:
    """An unchanged library implementation with a generated per-adopter proof."""

    case: str


@dataclass(frozen=True)
class ProvenOnce:
    """An unchanged library implementation proven by the named shared test."""

    test: str


@dataclass(frozen=True)
class NoApplicationCode:
    """A library-only operation with no per-identity application callback."""

    because: str


type LibraryProof = Generated | ProvenOnce | NoApplicationCode

# Inventory of the LIBRARY implementations, retained for upstream proof-location
# checks. A phase alone never grants this coverage: library_recovery_proofs must
# first match the actual executable callback to its library owner.
PHASE_PROOFS: dict[RecoveryPhase, LibraryProof] = {
    RecoveryPhase.DISCOVERY: Generated("test_one_poisoned_identity_does_not_stall_recovery[discovery-*]"),
    RecoveryPhase.REAP: Generated("test_one_poisoned_identity_does_not_stall_recovery[reap-*]"),
    RecoveryPhase.DISPATCH: Generated("test_one_poisoned_identity_does_not_stall_recovery[dispatch-*]"),
    RecoveryPhase.DEADLINE_WAKEUP: ProvenOnce(
        "integration_tests/capabilities/test_deadline_wakeups.py::test_failed_hint_does_not_block_due_work_or_other_hints"
    ),
    RecoveryPhase.EVIDENCE_SETTLEMENT: ProvenOnce(
        "package/tests/capabilities/test_operation_recovery_isolation.py"
        "::test_recovery_preserves_owed_evidence_and_isolates_only_identity_failures"
    ),
    RecoveryPhase.NONAPPLICATION: ProvenOnce(
        "package/tests/capabilities/test_operation_recovery_isolation.py"
        "::test_recovery_preserves_owed_evidence_and_isolates_only_identity_failures"
    ),
    RecoveryPhase.OBSERVATION: ProvenOnce(
        "package/tests/capabilities/test_observation_recovery.py"
        "::test_one_refused_observation_publication_does_not_hold_back_the_next"
    ),
    RecoveryPhase.CENSUS: NoApplicationCode("counts work; product counts are isolated at phase level"),
    RecoveryPhase.RETENTION: NoApplicationCode("deletes settled work rows by age, in bulk"),
    RecoveryPhase.REPAIR_WINDOW: NoApplicationCode("reopens the repair window by query, in bulk"),
    RecoveryPhase.STALE_WRITE_REOPEN: NoApplicationCode("reopens successes under row locks without product callbacks"),
    RecoveryPhase.ATTEMPT_RESOLUTION: NoApplicationCode("resolves attempt rows by query"),
    RecoveryPhase.ATTEMPT_RETENTION: NoApplicationCode("deletes resolved attempt rows by age, in bulk"),
    RecoveryPhase.ATTEMPT_CENSUS: NoApplicationCode("counts attempt rows"),
}


poison_recovery_call = poison_call


@dataclass(frozen=True, kw_only=True)
class RecoveryStepBinding:
    """The exact registered callback and its product example."""

    run: Callable[[RecoveryPass], int]
    example: Binding[RecoveryStepExample]


@contextmanager
def poison_paged_step(
    phase: RecoveryPhase, identities: Sequence[RecoveryIdentity], *, effect: str | None = None, step: str = ""
) -> Iterator[Callable[[], int]]:
    """The default fault: ``run_page`` raises for an armed identity before calling the step for it.

    The effect, phase and step key select the registered loop independently
    of other loops using the same phase. A loop that does not use ``run_page``
    never meets this fault, and the generated proof fails as unfired.
    """
    step_key = step
    original = RecoveryPass.run_page
    switch = PoisonSwitch()
    with poison_many(switch.poisoned, identities) as fired:

        def run_page(
            self: RecoveryPass, page_phase: RecoveryPhase, ordered_ids: Any, size: int, step: Callable[[Any], int]
        ) -> int:
            if page_phase != phase or (effect is not None and self.effect != effect) or self.step_key != step_key:
                return original(self, page_phase, ordered_ids, size, step)

            def faulted(identity: Any) -> int:
                switch.check(identity, f"{phase.value} recovery fault")
                return step(identity)

            return original(self, page_phase, ordered_ids, size, faulted)

        with patch.object(RecoveryPass, "run_page", run_page):
            yield fired


type RecoveryExampleBinding = RecoveryStepBinding | Binding[RecoveryStepExample]


def bound_recovery_steps(
    capability: DurableWorkCapability[Any, Any], bindings: Mapping[str, RecoveryExampleBinding]
) -> dict[str, RecoveryStepBinding]:
    """Paged registrations derive the executable callback; authors supply only product examples."""
    actual = {step.identity: step for step in custom_recovery_steps(capability)}
    resolved = {}
    for phase, binding in bindings.items():
        if isinstance(binding, RecoveryStepBinding):
            resolved[phase] = binding
        else:
            step = actual.get(phase)
            if step is None or not isinstance(step.run, RecoveryPage):
                raise ObligationContractDesignError(
                    f"{phase}: a plain product example requires RecoveryStep.paged; "
                    "an arbitrary callback needs RecoveryStepBinding(run=..., example=...)"
                )
            resolved[phase] = RecoveryStepBinding(run=step.run, example=binding)
    validate_recovery_bindings(
        {key: step.run for key, step in actual.items()},
        {key: binding.run for key, binding in resolved.items()},
    )
    return resolved


def recovery_steps(capability: DurableWorkCapability[Any, Any]) -> tuple[RecoveryStep, ...]:
    steps = (*capability.derivation_steps(), *capability.recorded_work_steps())
    keys = [step.identity for step in steps]
    if len(set(keys)) != len(keys):
        raise ObligationContractDesignError("recovery steps repeat a key; name each loop with its own stable key")
    return steps


def _implementation(run: Callable[..., Any]) -> object:
    if isinstance(run, MethodType):
        # Only the exact library descriptor gets value matching. Its model,
        # policy and retention travel with execution; closures never borrow a
        # proof through __code__, and subclass overrides are not library code.
        owner = run.__self__
        return (run.__func__, owner if type(owner) in (AttemptRecovery, ProjectionRecovery) else id(owner))
    return run


def library_recovery_proofs(
    capability: DurableWorkCapability[Any, Any],
) -> dict[tuple[RecoveryPhase, object], LibraryProof]:
    """Library-owned executable implementations and the proof family covering them.

    Factory steps are constructed without executing queries. Application
    overrides do not enter this inventory: methods are taken from their owning
    library classes, not resolved through the adopter instance.
    """
    from due.django.capabilities.authoring import OwnedDurableWorkCapability
    from due.django.capabilities.observed_operations import ObservedExternalOperation
    from due.django.capabilities.operations import DurableExternalOperation
    from due.django.capabilities.projection import DurableProjection, projection_recovery_steps
    from due.django.capabilities.scheduled import DurableScheduledAction

    methods = (
        (RecoveryPhase.REAP, DurableWorkCapability._reap),
        (RecoveryPhase.DISPATCH, DurableWorkCapability._dispatch),
        (RecoveryPhase.CENSUS, DurableWorkCapability._census),
        (RecoveryPhase.RETENTION, DurableWorkCapability._retain),
        (RecoveryPhase.DEADLINE_WAKEUP, DurableWorkCapability._wake_near_deadlines),
        (RecoveryPhase.DISCOVERY, OwnedDurableWorkCapability._discover),
        (RecoveryPhase.EVIDENCE_SETTLEMENT, DurableExternalOperation._settle_applied_evidence),
        (RecoveryPhase.NONAPPLICATION, DurableExternalOperation._reconcile_nonapplication_page),
        (RecoveryPhase.OBSERVATION, ObservedExternalOperation._recover_observations),
    )
    proofs = {(phase, _implementation(MethodType(run, capability))): PHASE_PROOFS[phase] for phase, run in methods}
    for step in projection_recovery_steps(capability.model, capability.policy, derivation=()):
        if step.phase in (RecoveryPhase.STALE_WRITE_REOPEN, RecoveryPhase.ATTEMPT_CENSUS):
            proofs[step.phase, _implementation(step.run)] = PHASE_PROOFS[step.phase]
    for step in attempt_recovery_steps(
        capability.model,
        capability.policy,
        retention=capability.attempt_retention
        if isinstance(capability, (DurableProjection, DurableScheduledAction))
        else DurableProjection.attempt_retention,
        keep_started_evidence=isinstance(capability, DurableScheduledAction),
        derivation=(),
    ):
        proofs[step.phase, _implementation(step.run)] = PHASE_PROOFS[step.phase]
    return proofs


#: Built-in methods whose adopter overrides are proven by the shared history of the same name.
_SHARED_HISTORY_METHODS = {RecoveryPhase.REAP: "_reap", RecoveryPhase.DISPATCH: "_dispatch"}


def _overrides_shared_history(capability: DurableWorkCapability[Any, Any], step: RecoveryStep) -> bool:
    """Whether ``step`` is this capability's own override of ``_reap``/``_dispatch``, in that phase.

    The shared reap and dispatch histories fault the seams any reaper or
    dispatcher must reach (claim settlement hooks; the broker publication)
    through the registered tick, and fail when that fault never fires. So an
    override needs no declaration: one that bypasses those seams fails its
    generated case instead of inheriting a proof by name.
    """
    name = _SHARED_HISTORY_METHODS.get(step.phase)
    run = step.run
    return (
        step.key is None
        and name is not None
        and isinstance(run, MethodType)
        and run.__self__ is capability
        and run.__func__ is getattr(type(capability), name)
    )


def custom_recovery_steps(capability: DurableWorkCapability[Any, Any]) -> tuple[RecoveryStep, ...]:
    """Steps that need an adopter binding: neither a library implementation nor a shared-history override."""
    library = library_recovery_proofs(capability)
    return tuple(
        step
        for step in recovery_steps(capability)
        if (step.key is not None or (step.phase, _implementation(step.run)) not in library)
        and not _overrides_shared_history(capability, step)
    )


def validate_recovery_step_bindings(
    capability: DurableWorkCapability[Any, Any], bindings: Mapping[str, RecoveryExampleBinding]
) -> None:
    bound_recovery_steps(capability, bindings)


def recovery_histories(capability: DurableWorkCapability[Any, Any]) -> tuple[str, ...]:
    library = library_recovery_proofs(capability)
    histories = []
    for step in recovery_steps(capability):
        proof = library.get((step.phase, _implementation(step.run))) if step.key is None else None
        if isinstance(proof, Generated) or _overrides_shared_history(capability, step):
            histories.append(step.phase.value)
        elif proof is None:
            histories.append(f"step:{step.identity}")
    return tuple(histories)
