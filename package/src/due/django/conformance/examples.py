"""Public application examples for Due's generated conformance suite.

Factories arrange real production obligations and independent observations.
They may return an example directly or yield it from a context manager; the
scope remains open through the final assertion. These declarations never select,
claim, settle or recover work: the bound capability owns those transitions.

Start with ``EffectExamples`` and ``DeliveryExample``. See
``docs/conformance-api.md`` for field requirements, scope and extension points.
"""

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from pytest_obligation.binding import is_real_reason
from pytest_obligation.completion import BoundedCompletion, DeliveryReplay
from pytest_obligation.contract import (
    NotApplicable,
)
from pytest_obligation.crash_histories import HandoffHistory
from pytest_obligation.integrations.django.owed_rows import EdgeFamily
from pytest_obligation.interleavings.bindings import Scenario
from pytest_obligation.process_histories import ProcessHistory
from pytest_obligation.profiles.catalog import ConvergenceFamily
from pytest_obligation.profiles.eventual_convergence import (
    ConvergentWrite,
)
from pytest_obligation.profiles.harmless_replay import ReplaySafeEffect

from due.django.conformance.binding import Binding
from due.django.conformance.composition import (
    CompensationExample,
)
from due.django.conformance.composition import (
    SupersessionExample as SupersessionExample,
)
from due.django.conformance.observations import (
    ObservationExample,
)
from due.django.conformance.scenarios import (
    AdmissionFailureExample as AdmissionFailureExample,
)
from due.django.conformance.scenarios import (
    ProducerExample as ProducerExample,
)
from due.django.conformance.scenarios import (
    SynchronousCallerExample as SynchronousCallerExample,
)
from due.django.public import (
    UniverseDurableWork,
)
from due.errors import WorkFailureDisposition

__all__ = [
    "DeliveryExample",
    "EffectExamples",
    "EligibilityExample",
    "FactExamples",
    "FailureExample",
    "RejectionExample",
    "TerminalExample",
]


@dataclass(frozen=True)
class DeliveryExample[W: UniverseDurableWork, ObservationT]:
    """An admitted work row and an independent oracle for its external effect.

    ``work`` comes from the real producer. ``observe`` reads product/provider
    state; ``expected`` is the value that successful recovery must produce.
    ``effect_calls`` optionally counts actual external entries to detect replay.
    A work-row state flag alone cannot establish the product outcome.
    """

    work: W
    observe: Callable[[], ObservationT]
    expected: ObservationT
    effect_calls: Callable[[], int] | None = None


@dataclass(frozen=True)
class RejectionExample[W: UniverseDurableWork, ObservationT]:
    """A real provider rejection and its product outcome, composed with delivery setup."""

    delivery: DeliveryExample[W, ObservationT]
    disposition: WorkFailureDisposition
    expected_attempts: int = 1


@dataclass(frozen=True)
class EligibilityExample[W: UniverseDurableWork, ObservationT]:
    """A blocked admission and the production transition that makes it eligible.

    ``make_eligible`` must be a production callable accepted by the binding guard.
    ``recovery_timeout`` bounds scheduler recovery after that transition; keep
    provider seams alive through both the blocked and eligible observations.
    """

    delivery: DeliveryExample[W, ObservationT]
    make_eligible: Callable[[], None]
    recovery_timeout: timedelta = timedelta(minutes=5)


@dataclass(frozen=True, kw_only=True)
class FactExamples[W: UniverseDurableWork]:
    """Canonical product fixtures/actions; no derivation or settlement plumbing."""

    make_implied: Callable[[], Any]
    move: Callable[[Any], Any]
    make_stopped: Callable[[], Any]
    desired: Callable[[W], Any]
    identity_after_move: Callable[[Any], Any] = lambda identity: identity


@dataclass(frozen=True)
class FailureExample[W: UniverseDurableWork]:
    """Observe attempt count while a scoped dependency makes execution fail.

    ``attempts(work)`` reads persisted evidence or an independent provider counter. ``prepare(work)`` optionally
    arranges the failure before the runner starts; it must not replace retries.
    Prefer ``failing_external_call`` for a single provider seam.
    """

    attempts: Callable[[W], int]
    prepare: Callable[[W], object] = lambda work: None


@dataclass(frozen=True, kw_only=True)
class TerminalExample[W: UniverseDurableWork]:
    """
    What the product shows once the runtime gives up, and how it gets another chance.

    ``observe`` reads product state (not the work row) and must change when the
    work exhausts: an operator or user has to be able to see the failure.
    ``rearm`` is the authorized product command that re-opens the stopped work,
    normally ``with capability.atomic(owner_id) as pending: pending.retry_failed()``;
    the harness checks it starts a fresh revision and budget. A capability that
    must never re-arm (an immutable provider occurrence) states
    ``no_rearm_because`` instead.
    """

    observe: Callable[[W], Any]
    rearm: Callable[[W], object] | None = None
    no_rearm_because: str | None = None

    def __post_init__(self) -> None:
        if (self.rearm is None) == (self.no_rearm_because is None):
            raise ValueError("terminal example must declare exactly one of rearm or no_rearm_because")
        if self.no_rearm_because is not None and not is_real_reason(self.no_rearm_because):
            raise ValueError("no_rearm_because must name the product rule that forbids re-arming")


@dataclass(frozen=True, kw_only=True)
class EffectExamples[W: UniverseDurableWork]:
    """Application evidence consumed by ``effect_contract_suite``.

    ``make_work``, ``delivery`` and ``retry`` are required; the capability's mode
    and policy determine additional bindings. Omitted evidence is an unassessed
    obligation, never an implicit pass. Named mappings identify distinct product
    paths and appear in generated case/report identities.

    Ordinary bindings run inside ``environment``. Producer, admission and inline
    bindings own their scopes so early publications remain observable. ``fixtures``
    requests pytest fixtures for the generated cases; real process histories own
    child setup. See ``docs/conformance-api.md`` for every field and its scope.
    """

    in_flight: Mapping[str, Scenario] = field(default_factory=dict)
    evidence_confluence: Mapping[str, Scenario] = field(default_factory=dict)
    make_work: Callable[[], W]
    delivery: Binding[DeliveryExample[W, Any]]
    retry: Binding[FailureExample[W]] | None = None
    terminal: Binding[TerminalExample[W]] | None = None
    replay: Binding[ReplaySafeEffect] | None = None
    eligibility: Binding[EligibilityExample[W, Any]] | Mapping[str, Binding[EligibilityExample[W, Any]]] | None = None
    # A checked domain assertion: admitted inputs cannot move when product facts change.
    immutable_inputs: str | None = None
    # Arrange changed product facts; generated cases replay every bound admission path.
    perturb_inputs: Callable[[W], object] | None = None
    # Honest binding debt when inputs move but the E families are not yet bound.
    convergence_not_assessed: str | None = None
    snapshot: Binding[SupersessionExample[W, Any]] | None = None
    derivation: Binding[FactExamples[W]] | None = None
    # Related rows the discovery reads (a parent's scope the candidate query joins, a tenant desired_for
    # copies), by family name: each family's edge_row maps the owner to the row whose database-legal
    # edge values the discovery admissibility cases write. Owner fields are always perturbed.
    discovery_edges: Mapping[str, EdgeFamily] = field(default_factory=dict)
    applied_receipt: Callable[[W], Any] | None = None
    # The application's production entry point for a late provider receipt (its
    # webhook handler or reconciliation command), called with the work and
    # applied_receipt(work). When bound, crash-ambiguity proofs settle through it
    # and registered recovery instead of the library's evidence recorder.
    evidence_entry: Callable[[W, Any], object] | None = None
    receipt_repair: Binding[DeliveryExample[W, Any]] | None = None
    producers: dict[str, Binding[ProducerExample[W, Any]]] = field(default_factory=dict)
    admissions: dict[str, Binding[ProducerExample[W, Any]]] = field(default_factory=dict)
    admission_failures: dict[str, Binding[AdmissionFailureExample[W, Any]]] = field(default_factory=dict)
    # Profile I is NotApplicable only when admission has no partial state to roll
    # back: one work-row write, committed atomically with no product row. A
    # command that also writes product rows binds admission_failures.
    admission_not_applicable: str | None = None
    # Profile E, MONOTONIC_RESULTS: the production result-application transition.
    convergence: Binding[ConvergentWrite] | None = None
    # Profile E families that genuinely do not apply; an unbound, undeclared family stays NotAssessed.
    convergence_families: Mapping[ConvergenceFamily, NotApplicable] = field(default_factory=dict)
    # Request-path entry points that admit and then attempt inline; see SynchronousCallerExample.
    synchronous_callers: dict[str, Binding[SynchronousCallerExample[Any]]] = field(default_factory=dict)
    rejections: Mapping[str, Binding[RejectionExample[W, Any]]] | None = None
    uncertainties: Mapping[str, Binding[DeliveryExample[W, Any]]] | None = None
    observation: Binding[ObservationExample[W, Any, Any]] | None = None
    identity_of: Callable[[W], Any] | None = None
    environment: Callable[[], AbstractContextManager] = nullcontext
    fixtures: tuple[str, ...] = ()
    compensation: Binding[CompensationExample[W, Any]] | None = None
    # One shared fault runner for native and work-table contracts: this adds
    # product bindings, not a second recovery loop. The registered sweep runs
    # the real workers even when the transition hands off to another capability.
    handoffs: tuple[HandoffHistory[Any, Any], ...] = ()
    process_handoffs: tuple[ProcessHistory[Any, Any], ...] = ()
    completions: Mapping[str, Binding[BoundedCompletion]] = field(default_factory=dict)
    delivery_replays: Mapping[str, Binding[DeliveryReplay]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for history in self.process_handoffs:
            history.require_expected_outcome()
        for name, family in self.discovery_edges.items():
            if not name.strip() or "[" in name or "]" in name:
                raise ValueError(f"discovery_edges family name {name!r} must be a nonempty case-id fragment")
            if family.edge_row is None:
                raise ValueError(
                    f"discovery_edges[{name!r}] declares no edge_row: the owner's own fields are already perturbed, "
                    f"so a family names the related row the discovery reads"
                )
        if self.discovery_edges and self.derivation is None:
            raise ValueError("discovery_edges perturb the derivation example's owners; declare derivation")
