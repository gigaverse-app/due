"""
Profile E's ``immutable_inputs`` decline, checked against production instead of trusted as prose.

A work-table contract that claims no E family declines E with
``EffectExamples.immutable_inputs``: "production never moves the desired inputs
of work it has admitted". Profile E exists for the opposite case, so the decline
is only as good as that sentence. Until this check it was a string nothing read,
and a mapping found a contract whose production contradicted it (a wake
superseding claimed work), the same class as the membership contract whose
"no retry limit" ``NotApplicable`` hid a ten-attempt budget.

Which capabilities need the check. Work-table inputs move in two ways, both in
the library: ``request_work`` with different domain fields rewrites them and
supersedes the revision, and ``wake_work`` supersedes a claimed revision so the
next pass reads facts that moved. Evidence-required operations refuse both
(``request_work`` raises on a different input, ``supersede_work`` and
``wake_work`` raise for the model), and an ``IndependentCompensation`` has no
admission seam to re-request and cannot be woken (``wakeable=False``, enforced
when registrations are indexed). Their decline is true by construction, so only
owned replay-safe capabilities are checked, and only they bind ``perturb_inputs``.

The check, per bound real admission path (``producers``, ``admissions``,
``admission_failures``' own admission and ``synchronous_callers``):

1. No registered capability declares this work as a ``Dependent``. A declared
   wake runs on every upstream settlement and supersedes this work while it is
   claimed: the design says these inputs move, so the decline is refused without
   arranging the upstream settlement.
2. Admit through the path, then claim the admitted work through production's
   claim (``claim_work`` with the capability's eligibility): the work is in flight.
   Work its execution gate parks cannot be claimed yet and is revisited parked.
3. ``perturb_inputs(work)`` moves the product facts the inputs derive from.
   Positive control: it must write at least one row outside the work table.
4. Re-run the same path, then the registered recovery tick (reap, dispatch and
   discovery). A refusal raised by the path is production's answer and is kept
   for the failure message; the oracle still runs. Positive control: a second
   call that only admitted fresh obligations, without entering ``atomic()`` for
   an admitted owner, revisited nothing.
5. The oracle, root-owned: each admitted row still exists with the same domain
   fields and desired revision, and a claimed row keeps its state and fencing
   token (recovery may legitimately dispatch a parked row whose gate opened). Any difference means
   production moved inputs of in-flight work, and the contract must bind the E
   families instead.

The perturbation is arrange. It may write the derivation source directly, even
where no production command writes it today: the claim is that the obligation
freezes its inputs, so a capability that re-derives them on every revisit is
refused although the facts happen to be write-once elsewhere. Prefer the
production command where one exists, so a wake inside it is observed as well.
What stays with the reviewer: whether ``perturb_inputs`` moves the facts the
inputs are derived from. A perturbation of an unrelated column passes vacuously.
"""

from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import UUID

import pytest
from django.db import connection, transaction
from pytest_obligation.integrations.django.writes import row_write

from due.django.conformance.binding import entered_binding
from due.django.conformance.scenarios import (
    AdmissionFailureExample,
    ProducerExample,
    SynchronousCallerExample,
)
from due.django.public import OwnedDurableWorkCapability, UniverseDurableWork, claim_work
from due.django.runtime.coordination import CLAIMABLE_STATES
from due.django.runtime.registry import installed, registration_for
from due.django.runtime.tasks import recover_durable_work_task, run_durable_work_task
from due.django.testing import hold_nudges, make_due

if TYPE_CHECKING:
    from due.django.conformance.suite import EffectExamples


@dataclass(frozen=True)
class AdmissionPath:
    """One bound production admission path, named ``<kind>-<declared name>``."""

    kind: str
    name: str
    binding: Callable[[], Any]

    @property
    def id(self) -> str:
        return f"{self.kind}-{self.name}"


def checks_immutable_inputs(capability: object) -> bool:
    """Whether production could move this capability's inputs, so an immutable-input decline needs the check."""
    return isinstance(capability, OwnedDurableWorkCapability) and not capability.model.requires_effect_evidence


def admission_paths(examples: EffectExamples[Any]) -> tuple[AdmissionPath, ...]:
    """Every bound real admission path, in declaration order; the generated case IDs."""
    groups: tuple[tuple[str, Mapping[str, Callable[[], Any]]], ...] = (
        ("producer", examples.producers),
        ("admission", examples.admissions),
        ("admission_failure", examples.admission_failures),
        ("synchronous_caller", examples.synchronous_callers),
    )
    return tuple(AdmissionPath(kind, name, binding) for kind, bindings in groups for name, binding in bindings.items())


type _RowState = tuple[tuple[tuple[str, object], ...], int, str | None, object]


def _row_state(capability: OwnedDurableWorkCapability[Any, Any, Any], pk: int, *, claimed: bool) -> _RowState | None:
    """Inputs and revision; for a claimed row also the claim, which no revisit may fence."""
    row = capability.model.objects.filter(pk=pk).first()
    if row is None:
        return None
    inputs = tuple((field.name, field.value_from_object(row)) for field in capability.domain_fields)
    if not claimed:
        return inputs, row.desired_revision, None, None
    return inputs, row.desired_revision, row.state, row.fencing_token


def _revisions(capability: OwnedDurableWorkCapability[Any, Any, Any]) -> dict[int, int]:
    return dict(capability.model.objects.values_list("pk", "desired_revision"))


def assert_no_declared_wake_targets(capability: OwnedDurableWorkCapability[Any, Any, Any], name: str) -> None:
    """Refuse the decline when a registered capability declares this work as a dependent it wakes."""
    upstream = sorted(
        registration.name
        for registration in installed.get().values()
        if any(dependent.work is capability.model for dependent in registration.capability.wakes)
    )
    assert not upstream, (
        f"{name}: immutable_inputs is false: {', '.join(upstream)} declare(s) {capability.work_name} as a "
        f"Dependent, so every upstream settlement wakes it to re-read facts that moved, and wake_work supersedes "
        f"it while it is claimed. Inputs move while this work is in flight: bind the profile E families "
        f"(snapshot=, in_flight=, convergence=, evidence_confluence=) instead, or declare "
        f"convergence_not_assessed with the reason until they are bound"
    )


@contextmanager
def _entered_owners(capability: OwnedDurableWorkCapability[Any, Any, Any]) -> Iterator[list[int | str | UUID]]:
    """Record each owner production opens this capability's ``atomic()`` for; behaviour is unchanged."""
    owners: list[int | str | UUID] = []
    owned = type(capability)
    original = owned.atomic

    def spy(
        self: OwnedDurableWorkCapability[Any, Any, Any], owner_id: int | str | UUID, *args: Any, **kwargs: Any
    ) -> Any:
        if self.model is capability.model:
            owners.append(owner_id)
        return original(self, owner_id, *args, **kwargs)

    with patch.object(owned, "atomic", spy):
        yield owners


@contextmanager
def _product_writes(work_table: str) -> Iterator[Counter[str]]:
    """Count rows each statement wrote outside the work table: the perturbation's positive control."""
    written: Counter[str] = Counter()

    def record(execute: Callable[..., Any], sql: str, params: Any, many: bool, context: dict[str, Any]) -> Any:
        result = execute(sql, params, many, context)
        write = row_write(sql, context["cursor"])
        if write is not None and write.target != work_table:
            written[str(write)] += write.rows
        return result

    with connection.execute_wrapper(record):
        yield written


def _admit(path: AdmissionPath, example: object) -> Callable[[], object]:
    """Admit once through the path and return the call that re-runs it unchanged."""
    if isinstance(example, AdmissionFailureExample):
        example = example.admission
    if isinstance(example, SynchronousCallerExample):
        # The effect fails, so the accepted change stays owed instead of completing inline.
        with example.fail_effect(), pytest.raises(example.failure) if example.failure else nullcontext():
            example.call()
        return example.call
    assert isinstance(example, ProducerExample), f"{path.id}: unsupported admission binding {type(example)}"
    if path.kind == "producer":
        # Producers join their caller's transaction, as the producer-transaction proof runs them.
        def produce_in_caller_transaction() -> object:
            with transaction.atomic():
                return example.produce()

        produce_in_caller_transaction()
        return produce_in_caller_transaction
    example.produce()
    return example.produce


def assert_immutable_inputs_hold[W: UniverseDurableWork](
    capability: OwnedDurableWorkCapability[Any, W, Any],
    examples: EffectExamples[W],
    path: AdmissionPath,
    *,
    name: str,
) -> None:
    """Production must not move the inputs of admitted, in-flight work; see the module docstring."""
    perturb = examples.perturb_inputs
    assert perturb is not None, f"{name}: immutable_inputs is checked through perturb_inputs"
    assert_no_declared_wake_targets(capability, name)
    work_table = capability.model._meta.db_table
    with (
        hold_nudges(),
        patch.object(run_durable_work_task, "apply_async"),
        _entered_owners(capability) as entered,
        entered_binding(path.binding) as example,
    ):
        before = _revisions(capability)
        revisit = _admit(path, example)
        after_admission = _revisions(capability)
        admitted = sorted(pk for pk, revision in after_admission.items() if before.get(pk) != revision)
        assert admitted, f"{name}: positive control: {path.id} admitted no {capability.work_name} obligation"
        works = list(capability.model.objects.filter(pk__in=admitted).order_by("pk"))
        in_flight: dict[int, bool] = {}
        for work in works:
            make_due(work)
            claimed = claim_work(
                capability.model,
                work.pk,
                lease_duration=capability.policy.lease_duration,
                eligibility=capability.eligibility,
            )
            work.refresh_from_db()
            # Gated work that its execution gate parks is owed but cannot be in flight
            # yet: production revisits it parked, so the check does too.
            parked = claimed is None and capability.eligibility is not None and work.state in CLAIMABLE_STATES
            assert claimed is not None or parked, (
                f"{name}: positive control: {path.id} admitted {capability.work_name}:{work.pk} "
                f"({work.state}) that no worker can claim; the check revisits work while it is in flight"
            )
            in_flight[work.pk] = claimed is not None
        claimed_state = {pk: _row_state(capability, pk, claimed=claimed) for pk, claimed in in_flight.items()}
        owners = {getattr(work, capability.owner_attname) for work in works}

        with _product_writes(work_table) as written:
            for work in works:
                perturb(work)
        assert written, (
            f"{name}: positive control: perturb_inputs wrote no product row, so the revisit would only "
            f"prove that nothing moved; move the facts {capability.work_name}'s inputs are derived from"
        )

        entered.clear()
        refusal: Exception | None = None
        try:
            revisit()
        except Exception as error:  # noqa: BLE001 — a refusal is production's answer; the oracle still runs.
            refusal = error
        fresh = set(_revisions(capability)) - set(after_admission)
        revisited = owners.intersection(entered)
        recover_durable_work_task(registration_for(capability.model).name)

        after = {pk: _row_state(capability, pk, claimed=claimed) for pk, claimed in in_flight.items()}
        moved = {pk: (state, after[pk]) for pk, state in claimed_state.items() if after[pk] != state}
        refused = f"; the second call raised {refusal!r}" if refusal is not None else ""
        assert not moved, (
            f"{name}: immutable_inputs is false: {path.id} admitted the work and a worker claimed it (or its "
            f"execution gate parked it); after its facts moved, re-running {path.id} and recovery changed the "
            f"owed work{refused}. "
            + "; ".join(
                f"{capability.work_name}:{pk} (inputs, revision, state, token) {old} -> {new}"
                for pk, (old, new) in moved.items()
            )
            + ". Bind the profile E families this movement needs (snapshot=, in_flight=, convergence=, "
            "evidence_confluence=) instead of declaring immutable inputs, or declare convergence_not_assessed "
            "with the reason until they are bound"
        )
        assert revisited or refusal is not None or not fresh, (
            f"{name}: positive control: re-running {path.id} admitted fresh {capability.work_name} work "
            f"{sorted(fresh)} and never opened atomic() for an admitted owner, so it revisited nothing; bind the "
            f"path so a second call targets the same product aggregate"
        )
