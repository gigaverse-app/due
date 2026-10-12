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
from pytest_obligation.contract import ObligationContractDesignError

# pytest-global: Generated histories record the observations they actually assert, without application values.
from pytest_obligation.evidence.observation import assert_observation

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
    DurableWorkCapability,
    DurableWorkState,
    UniverseDurableWork,
    WorkClaim,
    WorkIncomplete,
    claim_work,
    database_now,
)

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
    caller-held transaction is exercised as production composes it. Its first
    invocation must owe a visible change of this capability.
    ``fail_effect`` makes the external boundary refuse; ``failure`` is the
    exception callers then see, or None when the entry point absorbs it.
    ``requires_inline`` declares that the call must refuse an enclosing
    transaction (complete()) instead of deferring (complete_or_defer()).
    ``repeat`` invokes the same entry point again with the value ``call``
    requested; it defaults to ``call`` itself, so bind it when ``call`` creates
    a new owner each time. ``lose_response`` lets the provider apply the effect
    and then fails the caller's request, as a timeout after acceptance does
    (``ProviderProbe.lose_response``); the response-lost history requires it.
    """

    call: Callable[[], object]
    observe: Callable[[], ObservationT]
    expected: ObservationT
    effect_calls: Callable[[], int]
    fail_effect: Callable[[], AbstractContextManager[object]]
    failure: type[Exception] | None
    requires_inline: bool = False
    repeat: Callable[[], object] | None = None
    lose_response: Callable[[], AbstractContextManager[object]] | None = None


# pytest-global: Generated synchronous-caller histories; this tuple creates no fixtures.
SYNCHRONOUS_CALLER_HISTORIES = (
    "inline",
    "enclosing-commit",
    "enclosing-rollback",
    "effect-failure",
    "claim-held",
    "repeat-same-value",
    "response-lost",
)


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

        if history == "repeat-same-value":
            _assert_repeat_admits_nothing(capability, example, entry, sent)
            return

        if history == "response-lost":
            _assert_lost_response_is_not_blindly_resent(capability, example, entry, sent)
            return

        with example.fail_effect():
            with pytest.raises(example.failure) if example.failure else nullcontext():
                example.call()
        owed = owed_ids()
        if history == "claim-held":
            _assert_caller_waits_for_the_held_claim(capability, example, entry, sent, owed)
            return
        assert history == "effect-failure", history
        assert owed, "a refused effect must leave the accepted change owed, not abandoned"
        assert not [args for args, _ in sent if args[0] == entry.name], "a failed inline attempt nudged its worker"
        failed_calls = example.effect_calls()
        _recover_lost_deliveries(entry, capability.model.objects.filter(pk__in=owed), sent)
        assert_observation(
            example.observe(), example.expected, because="recovery delivers the change the caller accepted"
        )
        assert example.effect_calls() > failed_calls, "recovery never reached the external boundary"


type _SentTask = tuple[tuple[str, int, str], dict[str, object]]


def _work_rows(model: type[UniverseDurableWork]) -> dict[int, tuple[object, ...]]:
    """Every work row's identity, revision and claim facts; a new row, revision or claim changes it."""
    return {
        row[0]: row[1:]
        for row in model.objects.values_list(
            "pk", "state", "desired_revision", "applied_revision", "fencing_token", "rearm_count"
        )
    }


def _assert_caller_waits_for_the_held_claim[W: UniverseDurableWork, ResultT, ObservationT](
    capability: DurableWorkCapability[W, ResultT],
    example: SynchronousCallerExample[ObservationT],
    entry: DurableWorkRegistration,
    sent: list[_SentTask],
    owed: set[int],
) -> None:
    """
    Another worker holds the claim of the accepted change when the caller runs again.

    The caller may take over only by fencing that claim (a new revision); while
    the claim is held it must not reach the provider. Either way the change is
    performed exactly once, after the holder dies and recovery delivers.
    """
    assert owed, "positive control: a refused effect must leave the accepted change owed"
    calls = example.effect_calls()
    claims: list[WorkClaim[W]] = []
    for work in capability.model.objects.filter(pk__in=owed):
        make_due(work)
        claim = claim_work(capability.model, work.pk, lease_duration=capability.policy.lease_duration)
        assert claim is not None, "positive control: another worker must hold the owed work's claim"
        claims.append(claim)
    try:
        example.call()
    except WorkIncomplete:
        # complete() reports the held claim; complete_or_defer() returns it as a status instead.
        assert example.requires_inline, "a deferring caller must report the held claim as a status, not raise"
    if example.effect_calls() > calls:
        held = [claim.work_id for claim in claims if claim.owned_work().exists()]
        assert not held, f"the caller reached the external boundary while another worker still held work {held}"
    owed |= set(outstanding_work(capability.model).values_list("pk", flat=True))
    _settle_through_recovery(entry, capability.model, owed, sent)
    assert_observation(
        example.observe(), example.expected, because="the held claim's change is delivered after its holder dies"
    )
    assert example.effect_calls() == calls + 1, (
        "the caller and the worker holding the claim must perform the accepted change exactly once"
    )


def _assert_repeat_admits_nothing[W: UniverseDurableWork, ResultT, ObservationT](
    capability: DurableWorkCapability[W, ResultT],
    example: SynchronousCallerExample[ObservationT],
    entry: DurableWorkRegistration,
    sent: list[_SentTask],
) -> None:
    """A repeat of the value already completed admits no revision, row, notification or effect."""
    example.call()
    assert_observation(example.observe(), example.expected, because="the first call completes the accepted change")
    rows = _work_rows(capability.model)
    settled = example.effect_calls()
    sent.clear()
    (example.repeat or example.call)()
    assert _work_rows(capability.model) == rows, (
        "a repeat of the same desired value must not admit new work, a new revision or a new claim; "
        "bind repeat= when call() creates a new owner each time"
    )
    assert example.effect_calls() == settled, "a repeat of the same desired value performed the effect again"
    assert not [args for args, _ in sent if args[0] == entry.name], "a repeat of a settled value notified its worker"
    recover_durable_work_task(entry.name)
    assert not [args for args, _ in sent if args[0] == entry.name], "a repeated settled value was redispatched"
    assert example.effect_calls() == settled
    assert_observation(example.observe(), example.expected, because="a repeat preserves the completed change")


def _assert_lost_response_is_not_blindly_resent[W: UniverseDurableWork, ResultT, ObservationT](
    capability: DurableWorkCapability[W, ResultT],
    example: SynchronousCallerExample[ObservationT],
    entry: DurableWorkRegistration,
    sent: list[_SentTask],
) -> None:
    """
    The provider accepted the inline attempt and the caller timed out.

    Evidence-required work must stay UNKNOWN and undispatched: recovery may not
    resend an effect that may have happened. Replay-safe work is replayed
    exactly once by recovery and must converge on the accepted change.
    """
    if example.lose_response is None:
        raise ObligationContractDesignError(
            f"{entry.name}: bind SynchronousCallerExample.lose_response: the provider applies the effect, "
            "then the caller's request fails (ProviderProbe.lose_response), so recovery's handling of an "
            "uncertain inline attempt is proven"
        )
    model = capability.model
    calls = example.effect_calls()
    with example.lose_response(), suppress(Exception):
        # The caller timed out; whatever it reports, its committed obligation is judged below.
        example.call()
    accepted = example.effect_calls()
    assert accepted == calls + 1, "positive control: the lost response must follow exactly one real provider entry"
    owed = set(outstanding_work(model).values_list("pk", flat=True))
    assert owed, "a lost response must leave the accepted change owed, not settled or abandoned"
    if not model.requires_effect_evidence:
        _settle_through_recovery(entry, model, owed, sent)
        assert_observation(
            example.observe(), example.expected, because="replaying an uncertain replay-safe effect converges"
        )
        assert example.effect_calls() == accepted + 1, "recovery must replay the uncertain effect exactly once"
        return
    for _ in range(2):
        for work in model.objects.filter(pk__in=owed):
            if work.state in (DurableWorkState.QUEUED, DurableWorkState.CLAIMED):
                expire_lease(work)
            # Backoff elapsed is not evidence of nonapplication.
            make_due(work)
        sent.clear()
        recover_durable_work_task(entry.name)
        for args, _ in sent:
            if args[0] == entry.name and args[1] in owed:
                with suppress(Exception):
                    run_durable_work_task(*args)
        states = set(model.objects.filter(pk__in=owed).values_list("state", flat=True))
        assert states == {DurableWorkState.UNKNOWN}, (
            f"an uncertain inline attempt left {sorted(states)}; only provider evidence may settle or retry it"
        )
        assert not due_work(model).filter(pk__in=owed).exists(), "uncertain work became dispatchable"
        assert example.effect_calls() == accepted, "recovery blindly resent an effect whose outcome was unknown"


def _settle_through_recovery(
    entry: DurableWorkRegistration,
    model: type[UniverseDurableWork],
    ids: set[int],
    sent: list[_SentTask],
    *,
    rounds: int = 3,
) -> None:
    """
    Lose every lease and notification, then let registered recovery deliver until the work settles.

    Each round expires reserved or active leases, ages due times, runs the
    registration's recovery task and executes exactly the deliveries it
    published for ``ids``. A failed delivery is judged by the state it leaves,
    as a real worker's would be.
    """
    for _ in range(rounds):
        pending = list(model.objects.filter(pk__in=ids).exclude(state=DurableWorkState.SUCCEEDED))
        if not pending:
            return
        for work in pending:
            if work.state in (DurableWorkState.QUEUED, DurableWorkState.CLAIMED):
                expire_lease(work)
            make_due(work)
        sent.clear()
        recover_durable_work_task(entry.name)
        for args, options in [(args, options) for args, options in sent if args[0] == entry.name and args[1] in ids]:
            assert_registered_worker_options(entry, options)
            with suppress(Exception):
                run_durable_work_task(*args)


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


# pytest-global: Every adopter with a wake command supplies it through this shared scenario contract.
@dataclass(frozen=True)
class WakeExample[W: UniverseDurableWork, ObservationT]:
    """
    A real command that commits new facts for existing work and wakes it.

    ``work`` was admitted by the real producer. ``wake`` is what callers invoke
    (a view, service method or webhook handler): it changes the product facts
    the work reads and calls the capability's ``wake()`` for that work; it must
    not re-request the work. ``expected`` is the outcome of the NEW facts, so a
    worker that settles from the admitted snapshot fails. ``effect_calls``
    counts real provider entries.
    """

    work: W
    wake: Callable[[], object]
    observe: Callable[[], ObservationT]
    expected: ObservationT
    effect_calls: Callable[[], int]


# pytest-global: Generated wake-command histories; this tuple creates no fixtures.
WAKE_HISTORIES = ("ready", "claimed", "succeeded")


def wakes_existing_work(capability: DurableWorkCapability[Any, Any]) -> bool:
    """Whether a command can wake this capability's existing work (evidence and compensation cannot)."""
    return capability.wakeable and not capability.model.requires_effect_evidence


def assert_wake_rereads_facts[W: UniverseDurableWork, ResultT, ObservationT](
    capability: DurableWorkCapability[W, ResultT],
    example: WakeExample[W, ObservationT],
    *,
    history: str,
) -> None:
    """
    A wake makes existing work re-read new facts, and recovery covers its lost notification.

    ``ready``: the woken work keeps its revision and budget and becomes due now.
    ``claimed``: another worker holds a claim that may have read the old facts;
    the wake fences it (production advances the revision of a claimed pass and
    keeps its budget). ``succeeded``: the settled revision reopens unchanged.
    In every history the wake's notification is lost and the holder (if any)
    dies; registered recovery must deliver one execution that settles from the
    new facts.
    """
    from due.django.capabilities import authoring
    from due.django.runtime.coordination import WakeAction

    assert wakes_existing_work(capability), f"{capability.work_name} work cannot be woken"
    entry = DurableWorkBindings(capability, lambda: pytest.fail("the wake example admits the work")).registration
    model = capability.model
    work = example.work
    assert type(work) is model, "wake example must use the capability model"
    actions: list[WakeAction] = []
    original = authoring.wake_work
    sent: list[_SentTask] = []

    def observed_wake(woken: type[W], work_id: int, **options: Any) -> WakeAction:
        action = original(woken, work_id, **options)
        if woken is model and work_id == work.pk:
            actions.append(action)
        return action

    def capture(*, args: tuple[str, int, str], **options: object) -> None:
        sent.append((args, options))

    with (
        patch.object(run_durable_work_task, "apply_async", capture),
        patch.object(authoring, "wake_work", observed_wake),
    ):
        make_due(work)
        holder: WorkClaim[W] | None = None
        if history == "succeeded":
            capability.attempt_execution(work.pk)
        elif history == "claimed":
            holder = claim_work(model, work.pk, lease_duration=capability.policy.lease_duration)
            assert holder is not None, "positive control: another worker must hold the work's claim"
        else:
            assert history == "ready", history
        work.refresh_from_db()
        arranged = {"ready": DurableWorkState.READY, "claimed": DurableWorkState.CLAIMED}.get(
            history, DurableWorkState.SUCCEEDED
        )
        assert work.state == arranged, f"positive control: the example work must be {arranged}, got {work.state}"
        revision, rearms = work.desired_revision, work.rearm_count
        calls = example.effect_calls()
        assert example.observe() != example.expected, "positive control: the new facts must owe a visible change"
        sent.clear()
        example.wake()
        assert len(actions) == 1, (
            f"the command woke this work {len(actions)} time(s); it must call the capability's wake() once, "
            "not re-request the work"
        )
        work.refresh_from_db()
        assert work.rearm_count == rearms, "a wake must not spend or refresh the retry budget"
        if history == "claimed":
            assert holder is not None
            assert actions[0] == WakeAction.FENCED and not holder.owned_work().exists(), (
                "a wake must fence the claimed pass that may have read the old facts"
            )
        else:
            assert work.desired_revision == revision, "a wake must not start a new revision"
        _settle_through_recovery(entry, model, {work.pk}, sent)
        work.refresh_from_db()
        assert work.state == DurableWorkState.SUCCEEDED, f"recovery left the woken work {work.state}"
        assert_observation(example.observe(), example.expected, because="the woken work settles from the new facts")
        assert example.effect_calls() == calls + 1, "the woken work must reach the provider exactly once"


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
