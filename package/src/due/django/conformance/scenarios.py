"""
Standard application-bound histories: producer transactions and receipt repair.

Adopters supply commands and independent observations, never recovery algorithms.
See docs/conformance.md#reusing-scenarios-without-duplicating-tests.
"""

from asyncio import CancelledError
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext, suppress
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from django.db import OperationalError, transaction

# pytest-global: Generated histories record the observations they actually assert, without application values.
from pytest_obligation.evidence.observation import assert_observation

# pytest-global: Shared generated scenarios exercise this production seam for every enrolled adopter.
from due.django.capabilities.compensation import IndependentCompensation

# pytest-global: Shared generated scenarios exercise this production seam for every enrolled adopter.
from due.django.capabilities.operations import DurableExternalOperation

# pytest-global: Shared generated scenarios exercise this production seam for every enrolled adopter.
from due.django.conformance.bindings import (
    DurableWorkBindings,
    assert_registered_worker_options,
)

# pytest-global: Shared generated scenarios exercise this production seam for every enrolled adopter.
from due.django.evidence.attempts import attempts_for

# pytest-global: Shared generated scenarios exercise this production seam for every enrolled adopter.
from due.django.models import EffectAttempt, EffectAttemptOutcome

# pytest-global: Shared generated scenarios exercise this production seam for every enrolled adopter,
# and every adopter's evidence history checks due-ness on the database clock its claim reads.
from due.django.public import (
    ContinueAfter,
    DurableWorkCapability,
    DurableWorkState,
    ReopenAfter,
    UniverseDurableWork,
    WorkExecutionContext,
    database_now,
)

# pytest-global: The lost-acknowledgement history kills a continuation at its real settlement seam.
from due.django.runtime import coordination

# pytest-global: Unknown outcomes must remain discoverable and cannot bypass the canonical no-resend gate.
from due.django.runtime.coordination import due_work, outstanding_work, supersede_work

# pytest-global: Shared generated scenarios exercise this production seam for every enrolled adopter.
from due.django.runtime.tasks import recover_durable_work_task, run_durable_work_task

# pytest-global: Shared generated scenarios exercise this production seam for every enrolled adopter.
from due.django.testing import WorkerDied, expire_lease, hold_nudges, make_due

# pytest-global: Shared evidence histories let adapters either propagate or handle definite rejection.
from due.errors import EffectNotAppliedError, EffectOutcomeUnknownError

if TYPE_CHECKING:
    # pytest-global: Reuse the enrollment example type across all generated receipt scenarios.
    from due.django.conformance.examples import DeliveryExample
    from due.django.runtime.registry import DurableWorkRegistration


# pytest-global: Every adopter supplies commands and observations through this shared scenario contract.
@dataclass(frozen=True)
class ProducerExample[W: UniverseDurableWork, ObservationT]:
    """Admission/revision; expected is committed intent or completion, according to enrollment."""

    produce: Callable[[], tuple[W, ...]]
    observe: Callable[[], ObservationT]
    expected: ObservationT
    effect_calls: Callable[[], int]
    # Create-only admission cannot target an existing aggregate. Bind its real
    # same-owner command for the immutable-input revisit; the oracle verifies entry.
    revisit: Callable[[tuple[W, ...]], object] | None = None


# pytest-global: Shared fault contract composes the existing producer example; no application setup on import.
@dataclass(frozen=True)
class AdmissionFailureExample[W: UniverseDurableWork, ObservationT]:
    """
    A fault after partial admission; the command must roll back its own writes.

    ``during`` installs an external fault/clock change and invokes the supplied
    checkpoint after real partial writes, before production raises expected_error.
    It must not supply a transaction, undo writes, or implement product decisions.
    """

    admission: ProducerExample[W, ObservationT]
    during: Callable[[Callable[[], None]], AbstractContextManager[None]]
    expected_error: type[Exception]

    @classmethod
    def after_save(
        cls, admission: ProducerExample[W, ObservationT], model: type[UniverseDurableWork]
    ) -> AdmissionFailureExample[W, ObservationT]:
        """Lose the connection right after a real ``model`` row is written."""
        return cls(admission, during=partial(_interrupt_after_save, model), expected_error=OperationalError)


@contextmanager
def _interrupt_after_save(model: type[UniverseDurableWork], checkpoint: Callable[[], None]) -> Iterator[None]:
    save = model.save

    def save_then_fail(work: UniverseDurableWork, *args: Any, **kwargs: Any) -> None:
        save(work, *args, **kwargs)
        checkpoint()
        raise OperationalError("connection lost before admission committed")

    with patch.object(model, "save", save_then_fail):
        yield


def admission_atomicity(capability, failure):
    """Bind the standalone command to pytest-obligation's admission oracle."""
    from django.db import connection
    from pytest_obligation.profiles.indivisible_admission import AdmissionAtomicity, AdmissionPublication

    @contextmanager
    def publications():
        events = []

        def capture(*args, **kwargs):
            events.append(AdmissionPublication(in_transaction=connection.in_atomic_block))

        with patch.object(run_durable_work_task, "apply_async", capture):
            yield events

    example = failure.admission

    def admit():
        works = example.produce()
        assert works and all(type(work) is capability.model for work in works)
        return tuple(work.pk for work in works)

    return AdmissionAtomicity(
        name=capability.model._meta.label_lower,
        admit=admit,
        observe=example.observe,
        expected=example.expected,
        obligations=lambda: capability.model.objects.values_list("pk", flat=True),
        outstanding=lambda: outstanding_work(capability.model).values_list("pk", flat=True),
        effects=example.effect_calls,
        publications=publications,
        during=failure.during,
        expected_error=failure.expected_error,
    )


def assert_admission_failure(capability, failure, *, interrupt: bool) -> None:
    """Interrupt partial command admission and check that its own transaction rolls back."""
    from pytest_obligation.profiles.indivisible_admission import (
        assert_interrupted_admission_rolls_back,
        assert_successful_admission_commits,
    )

    binding = admission_atomicity(capability, failure)
    proof = assert_interrupted_admission_rolls_back if interrupt else assert_successful_admission_commits
    proof(binding)


# pytest-global: One fault marker gives every adopter the same post-finalizer rollback history.
class _FailedSettlement(Exception):
    pass


def assert_producer_transaction[W: UniverseDurableWork, ResultT, ObservationT](
    capability: DurableWorkCapability[W, ResultT],
    example: ProducerExample[W, ObservationT],
    *,
    rollback: bool,
    recover: bool = True,
) -> None:
    """Prove admission atomically; optionally also recover every output to completion."""

    entry = DurableWorkBindings(capability, lambda: example.produce()[0]).registration
    sent: list[tuple[tuple[str, int, str], dict[str, object]]] = []

    def capture(*, args: tuple[str, int, str], **options: object) -> None:
        sent.append((args, options))

    before = example.observe()
    calls = example.effect_calls()
    # A projection edit admits a new revision, not a new row. Retain the whole
    # previous row so rollback proves inputs AND coordination were restored.
    existing = {row["id"]: row for row in capability.model.objects.values()}
    assert before != example.expected, "producer example must owe a visible change"
    with patch.object(run_durable_work_task, "apply_async", capture):
        with transaction.atomic():
            works = example.produce()
            assert works, "producer must admit at least one obligation"
            assert all(type(work) is capability.model for work in works)
            ids = {work.pk for work in works}
            assert len(ids) == len(works), "producer returned duplicate obligations"
            for work in works:
                if work.pk in existing:
                    revision = capability.model.objects.get(pk=work.pk).desired_revision
                    assert revision == work.desired_revision and revision > existing[work.pk]["desired_revision"], (
                        "producer must admit a fresh obligation or advance its revision"
                    )
            assert not sent, "worker notification escaped the producer transaction"
            assert example.effect_calls() == calls, "provider I/O escaped before commit"
            if rollback:
                transaction.set_rollback(True)
        if rollback:
            assert not sent, "rolled-back producer dispatched work"
            assert not capability.model.objects.filter(pk__in=ids - existing.keys()).exists(), (
                "rolled-back work survived"
            )
            for work_id in ids.intersection(existing):
                assert capability.model.objects.filter(pk=work_id).values().get() == existing[work_id], (
                    "rolled-back revision changed existing work"
                )
            assert_observation(
                example.observe(), before, because="caller rollback preserves product and external state"
            )
            assert example.effect_calls() == calls
            return

        assert set(outstanding_work(capability.model).filter(pk__in=ids).values_list("pk", flat=True)) == ids, (
            "producer lost committed obligations"
        )
        assert example.effect_calls() == calls, "provider I/O escaped admission before worker execution"
        if not recover:
            assert_observation(
                example.observe(),
                example.expected,
                because="committed admission preserves its required product outcome",
            )
            return

        deliveries = _recover_lost_deliveries(entry, works, sent)
        assert_observation(
            example.observe(), example.expected, because="producer recovery reaches its required product outcome"
        )
        settled_calls = example.effect_calls()
        assert settled_calls > calls, "producer recovery never reached the external boundary"
        for args, _ in deliveries:
            run_durable_work_task(*args)
        sent.clear()
        recover_durable_work_task(entry.name)
        assert not [args for args, _ in sent if args[0] == entry.name and args[1] in ids], (
            "settled work was redispatched"
        )
        assert_observation(
            example.observe(), example.expected, because="duplicate delivery preserves the product outcome"
        )
        assert example.effect_calls() == settled_calls, "settled producer work repeated provider I/O"


# pytest-global: Every synchronous adopter supplies its real entry point through this shared scenario contract.
@dataclass(frozen=True)
class SynchronousCallerExample[ObservationT]:
    """
    A real entry point that commits intent, then attempts it in the same call.

    ``call`` is what callers actually invoke (a view or public service method,
    never the private transaction seam), so an enclosing ``@aatomic`` or a
    caller-held transaction is exercised as production composes it. It must
    admit fresh work or a new revision of this capability on every call.
    ``fail_effect`` makes the external boundary refuse; ``failure`` is the
    exception callers then see, or None when the entry point absorbs it.
    ``requires_inline`` declares that the call must refuse an enclosing
    transaction (complete()) instead of deferring (complete_or_defer()).
    """

    call: Callable[[], object]
    observe: Callable[[], ObservationT]
    expected: ObservationT
    effect_calls: Callable[[], int]
    fail_effect: Callable[[], AbstractContextManager[object]]
    failure: type[Exception] | None
    requires_inline: bool = False


# pytest-global: Generated synchronous-caller histories; this tuple creates no fixtures.
SYNCHRONOUS_CALLER_HISTORIES = ("inline", "enclosing-commit", "enclosing-rollback", "effect-failure")


def assert_synchronous_caller[W: UniverseDurableWork, ResultT, ObservationT](
    capability: DurableWorkCapability[W, ResultT],
    example: SynchronousCallerExample[ObservationT],
    *,
    history: str,
) -> None:
    """
    Prove a request-path caller never does provider I/O before its admission
    commits, never races its own worker, and never strands accepted intent.
    """
    entry = DurableWorkBindings(
        capability, lambda: pytest.fail("synchronous callers admit through call()")
    ).registration
    sent: list[_SentTask] = []

    def capture(*, args: tuple[str, int, str], **options: object) -> None:
        sent.append((args, options))

    def owed_ids() -> set[int]:
        return set(outstanding_work(capability.model).values_list("pk", flat=True))

    before = example.observe()
    calls = example.effect_calls()
    assert before != example.expected, "synchronous caller example must owe a visible change"
    with patch.object(run_durable_work_task, "apply_async", capture):
        if history == "inline":
            example.call()
            assert_observation(
                example.observe(), example.expected, because="an inline caller completes its own admission"
            )
            assert example.effect_calls() > calls, "an inline caller never reached the external boundary"
            assert not [args for args, _ in sent if args[0] == entry.name], (
                "a caller that attempts inline must suppress its nudge, or its worker races the attempt"
            )
            sent.clear()
            recover_durable_work_task(entry.name)
            assert not [args for args, _ in sent if args[0] == entry.name], "completed work was redispatched"
            return

        if history in ("enclosing-commit", "enclosing-rollback"):
            rollback = history == "enclosing-rollback"
            if example.requires_inline and not rollback:
                # The caller's own contract refuses to defer: it must fail loudly
                # and the enclosing transaction must leave nothing behind.
                with pytest.raises(RuntimeError, match="requires autocommit"), transaction.atomic():
                    example.call()
                assert_observation(example.observe(), before, because="a refused inline caller leaves no change")
                assert example.effect_calls() == calls and not sent
                return
            with transaction.atomic():
                refusal = pytest.raises(RuntimeError, match="requires autocommit")
                with refusal if example.requires_inline else nullcontext():
                    example.call()
                assert example.effect_calls() == calls, "provider I/O ran before the enclosing transaction committed"
                assert not sent, "worker notification escaped the enclosing transaction"
                if rollback:
                    transaction.set_rollback(True)
            if rollback:
                assert not sent, "a rolled-back caller dispatched work"
                assert_observation(example.observe(), before, because="caller rollback preserves product state")
                assert example.effect_calls() == calls
                return
            notified = [(args, options) for args, options in sent if args[0] == entry.name]
            assert notified, "a deferred caller must restore the worker notification it suppressed"
            for args, options in notified:
                assert_registered_worker_options(entry, options)
                run_durable_work_task(*args)
            assert_observation(
                example.observe(), example.expected, because="the deferred worker completes the accepted change"
            )
            settled_calls = example.effect_calls()
            assert settled_calls > calls, "deferred completion never reached the external boundary"
            for args, _ in notified:
                run_durable_work_task(*args)
            assert example.effect_calls() == settled_calls, "a duplicate deferred delivery repeated provider I/O"
            return

        assert history == "effect-failure", history
        with example.fail_effect():
            with pytest.raises(example.failure) if example.failure else nullcontext():
                example.call()
        owed = owed_ids()
        assert owed, "a refused effect must leave the accepted change owed, not abandoned"
        assert not [args for args, _ in sent if args[0] == entry.name], "a failed inline attempt nudged its worker"
        failed_calls = example.effect_calls()
        _recover_lost_deliveries(entry, capability.model.objects.filter(pk__in=owed), sent)
        assert_observation(
            example.observe(), example.expected, because="recovery delivers the change the caller accepted"
        )
        assert example.effect_calls() > failed_calls, "recovery never reached the external boundary"


type _SentTask = tuple[tuple[str, int, str], dict[str, object]]


def _recover_lost_deliveries[W: UniverseDurableWork](
    entry: DurableWorkRegistration, works: Iterable[W], sent: list[_SentTask]
) -> list[_SentTask]:
    """
    Lose every post-commit notification, then let registered recovery deliver.

    Expire only lost reservations; production recovery performs every state
    transition and redispatch. Returns the deliveries it executed once.
    """
    ids = set()
    sent.clear()
    for work in works:
        work.refresh_from_db()
        ids.add(work.pk)
        if work.state == DurableWorkState.QUEUED:
            expire_lease(work)
        make_due(work)
    recover_durable_work_task(entry.name)
    deliveries = [(args, options) for args, options in sent if args[0] == entry.name and args[1] in ids]
    assert {args[1] for args, _ in deliveries} == ids, "recovery omitted a committed obligation"
    for args, options in deliveries:
        assert_registered_worker_options(entry, options)
        assert run_durable_work_task(*args), "recovered delivery did not execute"
    return deliveries


def acknowledgement_replays(capability: DurableWorkCapability[Any, Any]) -> bool:
    """
    Whether recovery replays an executed claim whose acknowledgement was lost.

    Evidence-required work proves receipt repair instead; compensation is born
    claimed by its producer; a zero retry budget exhausts on the lost claim.
    """
    return (
        not capability.model.requires_effect_evidence
        and not isinstance(capability, IndependentCompensation)
        and capability.policy.max_rearms != 0
    )


def assert_lost_acknowledgement_replay_converges[W: UniverseDurableWork, ResultT, ObservationT](
    capability: DurableWorkCapability[W, ResultT],
    example: DeliveryExample[W, ObservationT],
    *,
    reference: Callable[[], AbstractContextManager[DeliveryExample[W, ObservationT]]] | None = None,
    rounds: int = 3,
) -> None:
    """
    The effect ran, its acknowledgement was lost, and recovery replays it to one settled outcome.

    The worker dies inside settlement, after the real effect returned, so its
    success (or continuation) never commits and the claim is left to expire.
    Registered recovery must deliver the same revision again, and that replay
    must reach the effect boundary again: a recovery that never redelivers, or
    a redelivery that selects nothing to do, proves no replay.

    A completing execution must then show one logical effect (``expected``),
    settle, and stay settled without a third call. A continuing execution has
    only advanced one step, so its replay is compared with ``reference``: a
    fresh example given the same two executions without the fault must reach
    the same outcome with the same number of provider entries.
    """
    assert not capability.model.requires_effect_evidence, "evidence-required work proves receipt repair instead"
    assert type(example.work) is capability.model, "delivery example must use the capability model"
    effect_calls = example.effect_calls
    assert effect_calls is not None, "a lost-acknowledgement replay requires an independent provider-call observation"
    entry = DurableWorkBindings(capability, lambda: pytest.fail("the delivery example admits the work")).registration
    work = example.work
    assert example.observe() != example.expected, "positive control: the example must initially owe a visible effect"
    make_due(work)
    work.refresh_from_db()
    revision = work.desired_revision
    calls = effect_calls()
    settle = type(capability)._finalize_success
    apply = type(capability).apply
    executions = 0
    continued: list[bool] = []
    sent: list[_SentTask] = []

    def lose_acknowledgement(self: DurableWorkCapability[W, ResultT], current: W, result: ResultT) -> object:
        # The product write runs inside the settlement transaction that the death rolls back.
        if isinstance(settle(self, current, result), ReopenAfter):
            continued.append(True)
        raise WorkerDied("effect applied; its acknowledgement was lost before settlement committed")

    def counted_apply(
        self: DurableWorkCapability[W, ResultT], current: W, context: WorkExecutionContext[W]
    ) -> ResultT | ContinueAfter:
        nonlocal executions
        if current.pk == work.pk:
            executions += 1
        return apply(self, current, context)

    def lose_continuation(*args: object, **kwargs: object) -> bool:
        continued.append(True)
        raise WorkerDied("step applied; its continuation was lost before it committed")

    def capture(*, args: tuple[str, int, str], **options: object) -> None:
        sent.append((args, options))

    with (
        patch.object(run_durable_work_task, "apply_async", capture),
        patch.object(type(capability), "apply", counted_apply),
    ):
        with (
            patch.object(type(capability), "_finalize_success", lose_acknowledgement),
            patch.object(coordination, "continue_work", lose_continuation),
            pytest.raises(WorkerDied),
        ):
            capability.attempt_execution(work.pk)
        dead_calls = effect_calls()
        assert dead_calls > calls, f"{entry.name}: positive control: the lost-acknowledgement attempt reached no effect"
        work.refresh_from_db()
        assert work.state == DurableWorkState.CLAIMED and work.desired_revision == revision, (
            f"{entry.name}: positive control: a lost acknowledgement leaves its claim unsettled, got {work.state}"
        )
        for _ in range(rounds):
            work.refresh_from_db()
            if effect_calls() > dead_calls and work.state not in (DurableWorkState.QUEUED, DurableWorkState.CLAIMED):
                break  # The replayed execution ran and committed its outcome.
            if work.state in (DurableWorkState.QUEUED, DurableWorkState.CLAIMED):
                expire_lease(work)
            make_due(work)
            sent.clear()
            recover_durable_work_task(entry.name)
            for args, options in [(args, options) for args, options in sent if args[:2] == (entry.name, work.pk)]:
                assert_registered_worker_options(entry, options)
                # A failed replay is judged by the settled state below, as a real worker's would be.
                with suppress(Exception):
                    run_durable_work_task(*args)
        work.refresh_from_db()
        replayed = effect_calls()
        assert replayed > dead_calls, (
            f"{entry.name}: recovery never reached the effect boundary again after the lost acknowledgement: "
            "it stranded the applied work, or its redelivery selected nothing to do"
        )
        assert work.desired_revision == revision, f"{entry.name}: a lost acknowledgement must not start a new revision"
        # Count executions at the kernel boundary, separately from the provider entries each makes.
        assert executions == 2, (
            f"{entry.name}: recovery entered the effect {executions - 1} times after the lost "
            "acknowledgement, against exactly 1: it did not settle on the first replay"
        )
        if continued:
            assert work.state in (DurableWorkState.READY, DurableWorkState.SUCCEEDED), (
                f"{entry.name}: the replayed step never committed; recovery left it {work.state}"
            )
            assert reference is not None, f"{entry.name}: a continuing execution needs a reference delivery"
            actual = example.observe()
            with reference() as fresh:
                assert fresh.effect_calls is not None
                make_due(fresh.work)
                before = fresh.effect_calls()
                for _ in range(2):
                    capability.attempt_execution(fresh.work.pk)
                    make_due(fresh.work)
                assert_observation(
                    actual,
                    fresh.observe(),
                    because="a step replayed after a lost continuation matches two uninterrupted executions",
                )
                assert replayed - calls == fresh.effect_calls() - before, (
                    f"{entry.name}: the replayed step reached the provider {replayed - calls} time(s); "
                    f"two uninterrupted executions reach it {fresh.effect_calls() - before} time(s)"
                )
            return
        assert work.state == DurableWorkState.SUCCEEDED, (
            f"{entry.name}: the replayed effect never settled; recovery left it {work.state}"
        )
        assert_observation(
            example.observe(),
            example.expected,
            because="a replay after a lost acknowledgement leaves one logical effect",
        )
        sent.clear()
        recover_durable_work_task(entry.name)
        assert not [args for args, _ in sent if args[:2] == (entry.name, work.pk)], "settled work was redispatched"
        assert effect_calls() == replayed, f"{entry.name}: settled work repeated provider I/O"


# pytest-global: Both evidence kinds run the same fault histories; this tuple creates no fixtures.
EVIDENCE_RECOVERY_HISTORIES = ("live", "death", "late", "settlement_rollback")
RECEIPT_RECOVERY_HISTORIES = (*EVIDENCE_RECOVERY_HISTORIES, "settlement_error")


def assert_due_on_database_clock(work: UniverseDurableWork) -> None:
    """
    Fail with the cause when the next execution cannot claim the example.

    A claim compares ``available_at`` with PostgreSQL's clock and misses
    silently, so every later assertion would blame the effect instead.
    """
    work.refresh_from_db()
    lead = work.available_at - database_now()
    assert lead <= timedelta(0), (
        f"example work is not due on the database clock ({lead} early); a due time from "
        "timezone.now() can run ahead of PostgreSQL: pass not_before only for future times"
    )


def run_evidence_history[W: UniverseDurableWork, RunT, EvidenceT, StoredT, FinalT, ObservationT](
    capability: DurableWorkCapability[W, RunT],
    example: DeliveryExample[W, ObservationT],
    *,
    record_evidence: Callable[[EffectAttempt, EvidenceT], StoredT],
    record_method: str,
    finalizer_method: str,
    finalize: Callable[[W, FinalT], None],
    interruption: str,
) -> None:
    """Execute real I/O with a shared fault at evidence or product settlement."""
    work = example.work
    assert_due_on_database_clock(work)
    assert example.observe() != example.expected, "evidence history must initially owe a product result"
    before_settlement = example.observe()
    entered = False
    expired_state: str | None = None

    def record(operation: DurableWorkCapability[W, RunT], attempt: EffectAttempt, evidence: EvidenceT) -> StoredT:
        nonlocal entered, before_settlement, expired_state
        entered = True
        before_settlement = example.observe()
        if interruption == "late":
            expire_lease(work)
            capability.recover()
            work.refresh_from_db()
            expired_state = work.state
        result = record_evidence(attempt, evidence)
        if interruption == "death":
            raise WorkerDied("evidence committed before settlement")
        return result

    def fail_after_finalizer(operation: DurableWorkCapability[W, RunT], current: W, result: FinalT) -> None:
        finalize(current, result)
        if interruption == "settlement_error":
            raise _FailedSettlement()
        raise WorkerDied("product finalizer ran before transaction rollback")

    settlement_fault = (
        patch.object(type(capability), finalizer_method, fail_after_finalizer)
        if interruption in {"settlement_rollback", "settlement_error"}
        else nullcontext()
    )
    with hold_nudges(), patch.object(type(capability), record_method, record), settlement_fault:
        if interruption == "settlement_error":
            with pytest.raises(_FailedSettlement):
                capability.attempt_execution(work.pk)
            work.refresh_from_db()
            assert work.state == DurableWorkState.UNKNOWN
            assert example.observe() != example.expected, "failed settlement published the successful product outcome"
        elif interruption in {"death", "settlement_rollback"}:
            with pytest.raises(WorkerDied):
                capability.attempt_execution(work.pk)
            assert_observation(
                example.observe(), before_settlement, because="failed settlement must roll back product writes"
            )
            expire_lease(work)
        else:
            with suppress(EffectNotAppliedError):
                capability.attempt_execution(work.pk)
    assert entered, "the real execution must reach evidence persistence"
    if interruption == "late":
        assert expired_state == DurableWorkState.UNKNOWN, "recovery omitted the product outcome of an expired attempt"


def assert_receipt_repairs_product[W: UniverseDurableWork, ResultT, ObservationT](
    capability: DurableWorkCapability[W, ResultT],
    example: DeliveryExample[W, ObservationT],
    *,
    interruption: str = "settlement_error",
) -> None:
    """The accepted effect and canonical receipt survive every settlement history."""
    assert isinstance(capability, DurableExternalOperation), "receipt repair requires an external operation"
    assert type(example.work) is capability.model, "receipt example must use the capability model"
    assert example.effect_calls is not None, "receipt repair requires an independent provider-call observation"
    calls = example.effect_calls()
    example.work.refresh_from_db()
    initial_rearms = example.work.rearm_count
    with hold_nudges():
        run_evidence_history(
            capability,
            example,
            record_evidence=capability.record_applied_effect_evidence,
            record_method="record_applied_effect_evidence",
            finalizer_method="_finalize_applied",
            finalize=capability._finalize_applied,
            interruption=interruption,
        )
        assert example.effect_calls() == calls + 1, "receipt history skipped or repeated provider I/O"
        example.work.refresh_from_db()
        attempt = attempts_for(example.work).get(
            desired_revision=example.work.desired_revision, claim_token=example.work.fencing_token
        )
        assert attempt.outcome == EffectAttemptOutcome.APPLIED, "settlement loss discarded applied evidence"
        receipt = capability.evidence_store.read(attempt)
        assert attempt.repair_pending == (interruption != "live"), "receipt has the wrong repair debt"
        capability.recover()
        capability.recover()
    assert_observation(
        example.observe(), example.expected, because="receipt recovery reaches the required product outcome"
    )
    assert example.effect_calls() == calls + 1, "receipt repair repeated provider I/O"
    attempt.refresh_from_db()
    assert not attempt.repair_pending, "receipt repair never settled its debt"
    assert capability.evidence_store.read(attempt) == receipt, "receipt repair changed canonical evidence"
    example.work.refresh_from_db()
    assert example.work.state == DurableWorkState.SUCCEEDED
    assert example.work.rearm_count == initial_rearms, "receipt recovery consumed a retry"
    assert attempts_for(example.work).count() == 1, "receipt repair opened another attempt"


def assert_uncertainty_blocks_resend[W: UniverseDurableWork, ResultT, ObservationT](
    capability: DurableWorkCapability[W, ResultT],
    example: DeliveryExample[W, ObservationT],
) -> None:
    """An actual uncertain provider outcome retains debt regardless of retry budget."""
    assert isinstance(capability, DurableExternalOperation)
    assert example.effect_calls is not None, "uncertainty requires an independent provider-call observation"
    work = example.work
    assert_due_on_database_clock(work)
    rearms = work.rearm_count
    revision = work.desired_revision
    previous_attempts = set(attempts_for(work).values_list("pk", flat=True))
    assert not attempts_for(work).exclude(outcome=EffectAttemptOutcome.NOT_APPLIED).exists()
    calls = example.effect_calls()
    assert example.observe() != example.expected, "uncertainty must have an observable product consequence"
    with hold_nudges():
        # A cancelled provider await is a BaseException, like worker death;
        # it must pass the same retained-UNKNOWN/no-resend checks below.
        with suppress(EffectOutcomeUnknownError, WorkerDied, CancelledError):
            capability.attempt_execution(work.pk)
        work.refresh_from_db()
        if work.state == DurableWorkState.CLAIMED:
            expire_lease(work)
        new_attempt = attempts_for(work).exclude(pk__in=previous_attempts).get()
        assert new_attempt.outcome == EffectAttemptOutcome.UNKNOWN, "uncertainty was recorded as definite evidence"
        for _ in range(2):
            capability.recover()
            work.refresh_from_db()
            new_attempt.refresh_from_db()
            assert work.state == DurableWorkState.UNKNOWN, "uncertain work gained permission to retry or finish"
            assert_observation(
                example.observe(), example.expected, because="uncertainty preserves the required product outcome"
            )
            assert work.rearm_count == rearms, "uncertainty spent the retry budget"
            assert work.desired_revision == revision
            assert new_attempt.outcome == EffectAttemptOutcome.UNKNOWN
            assert new_attempt.repair_pending, "uncertain attempt lost its unresolved repair debt"
            assert attempts_for(work).filter(
                pk__in=previous_attempts, outcome=EffectAttemptOutcome.NOT_APPLIED
            ).count() == len(previous_attempts)
            assert set(attempts_for(work).values_list("pk", flat=True)) == previous_attempts | {new_attempt.pk}
            assert outstanding_work(capability.model).filter(pk=work.pk).exists(), "uncertain debt disappeared"
            assert not due_work(capability.model).filter(pk=work.pk).exists(), "uncertain work became dispatchable"
            assert example.effect_calls() == calls + 1, "uncertainty repeated provider I/O"
            # Backoff elapsed is not evidence of nonapplication.
            make_due(work)
        with pytest.raises(ValueError, match="evidence cannot be bypassed"):
            supersede_work(capability.model, work.pk)
        assert example.effect_calls() == calls + 1
