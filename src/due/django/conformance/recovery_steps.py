"""Match executable recovery steps to library proofs or adopter fault bindings.

A phase is a metric/deferral namespace, not an implementation identity. Reusing
its name cannot lend a library proof to an application callback.
"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from types import FunctionType, MethodType
from typing import Any
from unittest.mock import patch
from uuid import UUID

from pytest_obligation.contract import ObligationContractDesignError
from pytest_obligation.poison import PoisonSwitch

from due.django.capabilities.authoring import DurableWorkCapability
from due.django.conformance.binding import Binding
from due.django.runtime.metrics import RecoveryPhase
from due.django.runtime.recovery_pass import RecoveryPass, RecoveryStep

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


@dataclass(frozen=True, kw_only=True)
class RecoveryStepExample:
    """Product fixtures and observations; the shared proof owns ticks and faults.

    ``make_pending`` creates independent candidates in selection order, through
    real product transitions. ``pending`` observes the product obligation, not
    dispatch state. ``poison`` is optional: by default the fault is raised at
    ``RecoveryPass.run_page``'s per-row ``step(identity)`` call for this phase
    (see ``poison_paged_step``), so a step that pages through ``run_page`` needs
    no fault seam. Supply ``poison`` (usually ``poison_recovery_call``) for a
    step that pages another way, or to fault inside the row's own transaction.
    """

    make_pending: Callable[[], RecoveryIdentity]
    pending: Callable[[RecoveryIdentity], bool]
    poison: Callable[[Sequence[RecoveryIdentity]], AbstractContextManager[Callable[[], int]]] | None = None


@dataclass(frozen=True, kw_only=True)
class RecoveryStepBinding:
    """The exact registered callback and its product example."""

    run: Callable[[RecoveryPass], int]
    example: Binding[RecoveryStepExample]


@contextmanager
def _armed(identities: Sequence[RecoveryIdentity]) -> Iterator[tuple[PoisonSwitch, Callable[[], int]]]:
    """One switch armed for every identity, with their summed firing count."""
    switch = PoisonSwitch()
    with ExitStack() as stack:
        counts = [stack.enter_context(switch.poisoned(identity)) for identity in identities]
        yield switch, lambda: sum(count() for count in counts)


@contextmanager
def poison_recovery_call(
    target: object, attribute: str, identity_of: Callable[..., RecoveryIdentity], identities: Sequence[RecoveryIdentity]
) -> Iterator[Callable[[], int]]:
    """Fault one real per-row call; all other calls delegate unchanged.

    This is the test-only dynamic interception boundary, not an application
    selector or copied recovery implementation. Shared counters prove the
    declared seam fired for the arranged candidates.
    """
    original = getattr(target, attribute)
    with _armed(identities) as (switch, fired):

        def invoke(*args: Any, **kwargs: Any) -> Any:
            switch.check(identity_of(*args, **kwargs), f"{attribute} recovery fault")
            return original(*args, **kwargs)

        with patch.object(target, attribute, invoke):
            yield fired


@contextmanager
def poison_paged_step(phase: RecoveryPhase, identities: Sequence[RecoveryIdentity]) -> Iterator[Callable[[], int]]:
    """The default fault: ``run_page`` raises for an armed identity before calling the step for it.

    Phases are unique per capability (``recovery_steps``), so the phase alone
    selects the custom loop. A loop that does not page through ``run_page``
    never meets this fault, and the generated proof fails as unfired.
    """
    original = RecoveryPass.run_page
    with _armed(identities) as (switch, fired):

        def run_page(
            self: RecoveryPass, page_phase: RecoveryPhase, ordered_ids: Any, size: int, step: Callable[[Any], int]
        ) -> int:
            if page_phase != phase:
                return original(self, page_phase, ordered_ids, size, step)

            def faulted(identity: Any) -> int:
                switch.check(identity, f"{phase.value} recovery fault")
                return step(identity)

            return original(self, page_phase, ordered_ids, size, faulted)

        with patch.object(RecoveryPass, "run_page", run_page):
            yield fired


def recovery_steps(capability: DurableWorkCapability[Any, Any]) -> tuple[RecoveryStep, ...]:
    steps = (*capability.derivation_steps(), *capability.recorded_work_steps())
    phases = [step.phase for step in steps]
    if len(set(phases)) != len(phases):
        raise ObligationContractDesignError("recovery steps repeat a phase; each loop needs its own identity")
    return steps


def _implementation(run: Callable[..., Any]) -> object:
    # A genuine callable boundary: bound methods are recreated on each access;
    # library factories recreate closures. Compare their executable code, never
    # __name__, __module__, decorator metadata, or the claimed phase alone.
    if isinstance(run, MethodType):
        # A known implementation bound to another capability is not this
        # adopter's shared step, even if the dataclass instances compare equal.
        return (_implementation(run.__func__), id(run.__self__))
    return run.__code__ if isinstance(run, FunctionType) else run


def library_recovery_proofs(
    capability: DurableWorkCapability[Any, Any],
) -> dict[tuple[RecoveryPhase, object], LibraryProof]:
    """Library-owned executable implementations and the proof family covering them.

    Factory steps are constructed without executing queries. Application
    overrides do not enter this inventory: methods are taken from their owning
    library classes, not resolved through the adopter instance.
    """
    from due.django.capabilities._attempt_recovery import attempt_recovery_steps
    from due.django.capabilities.authoring import OwnedDurableWorkCapability
    from due.django.capabilities.observed_operations import ObservedExternalOperation
    from due.django.capabilities.operations import DurableExternalOperation
    from due.django.capabilities.projection import DurableProjection

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
        (RecoveryPhase.STALE_WRITE_REOPEN, DurableProjection._reopen_stale_writes),
        (RecoveryPhase.ATTEMPT_CENSUS, DurableProjection._attempt_census),
    )
    proofs = {(phase, _implementation(MethodType(run, capability))): PHASE_PROOFS[phase] for phase, run in methods}
    for step in attempt_recovery_steps(
        capability.model, capability.policy, retention=DurableProjection.attempt_retention, derivation=()
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
        name is not None
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
        if (step.phase, _implementation(step.run)) not in library and not _overrides_shared_history(capability, step)
    )


def validate_recovery_step_bindings(
    capability: DurableWorkCapability[Any, Any], bindings: Mapping[RecoveryPhase, RecoveryStepBinding]
) -> None:
    custom = {step.phase: step for step in custom_recovery_steps(capability)}
    missing, extra = custom.keys() - bindings.keys(), bindings.keys() - custom.keys()
    if missing or extra:
        raise ObligationContractDesignError(
            "recovery_steps must cover exactly the custom executable callbacks; "
            f"missing={sorted(str(phase) for phase in missing)}, stale={sorted(str(phase) for phase in extra)}. "
            "Bind each real step with RecoveryStepBinding(run=..., example=...)."
        )
    for phase, binding in bindings.items():
        # INVARIANT: a declaration for another callback (even under the same
        # phase/name) cannot certify the registered production implementation.
        actual = custom[phase].run
        same = binding.run is actual
        if isinstance(actual, MethodType):
            same = (
                isinstance(binding.run, MethodType)
                and binding.run.__func__ is actual.__func__
                and binding.run.__self__ is actual.__self__
            )
        if not same:
            raise ObligationContractDesignError(f"recovery_steps[{phase}] is bound to a different production callback")


def recovery_histories(capability: DurableWorkCapability[Any, Any]) -> tuple[str, ...]:
    library = library_recovery_proofs(capability)
    histories = []
    for step in recovery_steps(capability):
        proof = library.get((step.phase, _implementation(step.run)))
        if isinstance(proof, Generated) or _overrides_shared_history(capability, step):
            histories.append(step.phase.value)
        elif proof is None:
            histories.append(f"step:{step.phase.value}")
    return tuple(histories)
