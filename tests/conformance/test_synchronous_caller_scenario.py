"""The shared synchronous-caller histories accept correct callers and catch broken ones."""

from collections.abc import Callable, Iterator
from contextlib import nullcontext
from dataclasses import replace
from unittest.mock import patch

import pytest
from django.db import connection, transaction
from pytest_obligation.contract import ObligationContractDesignError

from due.django.conformance.registrations import durable_work_installed
from due.django.conformance.scenarios import (
    SYNCHRONOUS_CALLER_HISTORIES,
    SynchronousCallerExample,
    assert_synchronous_caller,
)
from due.django.public import CompletionErrors, CompletionStatus, DurableWorkState, WorkIncomplete
from due.django.runtime.registry import DurableWorkRegistration
from tests.harness import primitive as example
from tests.harness.operation import ProviderResult, Publication, PublicationWork
from tests.harness.operation import operation_environment as operation_environment
from tests.harness.primitive import (
    PrimitiveEffect,
    PrimitiveOwner,
    PrimitiveWork,
    primitive,
    record,
)
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]

type Observation = str


def applied_then_lost(work: PrimitiveWork) -> str:
    # EXTERNAL SEAM: the provider applies the effect, then its response never arrives.
    PrimitiveEffect.calls.append(work.payload)
    raise TimeoutError("provider accepted; response lost")


def caller_example(
    call: Callable[[PrimitiveOwner], object],
    *,
    failure: type[Exception] | None = ConnectionError,
    requires_inline: bool = False,
    repeat: Callable[[PrimitiveOwner], object] | None = None,
) -> SynchronousCallerExample[Observation]:
    owner = PrimitiveOwner.objects.create()
    return SynchronousCallerExample(
        call=lambda: call(owner),
        # OBSERVE: the product row; provider entries are counted separately.
        observe=lambda: PrimitiveOwner.objects.get(pk=owner.pk).value,
        expected="after",
        effect_calls=lambda: len(PrimitiveEffect.calls),
        # EXTERNAL SEAM: the provider boundary refuses; the library classifies it.
        fail_effect=lambda: patch.object(example, "call", side_effect=ConnectionError("provider refused")),
        failure=failure,
        requires_inline=requires_inline,
        repeat=(lambda: repeat(owner)) if repeat is not None else None,
        lose_response=lambda: patch.object(example, "call", applied_then_lost),
    )


def deferring_caller(owner: PrimitiveOwner) -> object:
    return primitive.complete_or_defer(record(owner=owner))


def strict_caller(owner: PrimitiveOwner) -> object:
    work = record(owner=owner)
    primitive.complete(work)
    return work


@pytest.mark.parametrize("history", SYNCHRONOUS_CALLER_HISTORIES)
@pytest.mark.parametrize("caller", ["defers", "requires_inline"])
def test_correct_callers_pass_every_history(caller: str, history: str) -> None:
    if caller == "defers":
        scenario = caller_example(deferring_caller)
    else:
        scenario = caller_example(strict_caller, requires_inline=True)
    assert_synchronous_caller(primitive, scenario, history=history)


def nudging_caller(owner: PrimitiveOwner) -> object:
    # Keeps the fast nudge although it attempts the work itself.
    return primitive.complete_or_defer(record(owner=owner, notify_worker=True))


def io_before_commit_caller(owner: PrimitiveOwner) -> object:
    with transaction.atomic():
        work = record(owner=owner)
        primitive.perform_effect(work)
    return primitive.complete_or_defer(work)


def silent_deferral_caller(owner: PrimitiveOwner) -> object:
    work = record(owner=owner)
    if connection.in_atomic_block:
        return None  # Defers without restoring the suppressed nudge.
    return primitive.complete_or_defer(work)


def abandoning_caller(owner: PrimitiveOwner) -> object:
    work = record(owner=owner)
    try:
        return primitive.complete_or_defer(work)
    except ConnectionError:
        PrimitiveWork.objects.filter(pk=work.pk).delete()
        raise


@pytest.mark.parametrize(
    ("call", "history", "defect"),
    [
        (nudging_caller, "inline", "suppress its nudge"),
        (io_before_commit_caller, "enclosing-commit", "before the enclosing transaction committed"),
        (silent_deferral_caller, "enclosing-commit", "restore the worker notification"),
        (abandoning_caller, "effect-failure", "left the accepted change owed|owed, not abandoned"),
    ],
    ids=["nudge-races-attempt", "io-before-commit", "deferral-without-nudge", "failure-abandons-intent"],
)
def test_broken_callers_are_rejected(call: Callable[[PrimitiveOwner], object], history: str, defect: str) -> None:
    with pytest.raises(AssertionError, match=defect):
        assert_synchronous_caller(primitive, caller_example(call), history=history)


def test_strict_completion_without_declaring_it_fails_loudly() -> None:
    # complete() inside a caller's transaction is the @aatomic defect: the
    # history surfaces the library's refusal instead of passing silently.
    scenario = caller_example(strict_caller)
    with pytest.raises(RuntimeError, match="autocommit"):
        assert_synchronous_caller(primitive, scenario, history="enclosing-commit")


def test_absorbed_failure_is_still_proved_owed() -> None:
    def owing_caller(owner: PrimitiveOwner) -> object:
        return primitive.complete_or_defer(record(owner=owner), errors=CompletionErrors.OWE)

    assert_synchronous_caller(primitive, caller_example(owing_caller, failure=None), history="effect-failure")


def forcing_caller(owner: PrimitiveOwner) -> object:
    # Runs the provider itself when its attempt could not claim the work.
    work = record(owner=owner)
    completion = primitive.complete_or_defer(work)
    if completion.status is not CompletionStatus.COMPLETED:
        primitive.perform_effect(work)
    return completion


def rewaking_caller(owner: PrimitiveOwner) -> object:
    # Reopens settled work on every call, so a repeat performs the effect again.
    work = record(owner=owner)
    if primitive.completion_status(work) is CompletionStatus.COMPLETED:
        with transaction.atomic():
            primitive.wake(work.pk)
    return primitive.complete_or_defer(work)


def abandoning_on_timeout_caller(owner: PrimitiveOwner) -> object:
    work = record(owner=owner)
    try:
        return primitive.complete_or_defer(work)
    except TimeoutError:
        # Treats the timeout as a definite failure and drops the accepted change.
        PrimitiveWork.objects.filter(pk=work.pk).delete()
        raise


def latest_created_value() -> str:
    # OBSERVE: the owner the creating caller made, or "before" until it has made one.
    if PrimitiveOwner.objects.count() < 2:
        return "before"
    latest = PrimitiveOwner.objects.order_by("pk").last()
    assert latest is not None
    return latest.value


def creating_caller(owner: PrimitiveOwner) -> object:
    # Each call creates another owner, so repeating call() is not a repeat of the same value.
    return deferring_caller(PrimitiveOwner.objects.create())


@pytest.mark.parametrize(
    ("call", "history", "defect"),
    [
        (forcing_caller, "claim-held", "still held work"),
        (rewaking_caller, "repeat-same-value", "must not admit new work, a new revision or a new claim"),
        (creating_caller, "repeat-same-value", "bind repeat="),
        (abandoning_on_timeout_caller, "response-lost", "owed, not settled or abandoned"),
    ],
    ids=["provider-call-under-held-claim", "repeat-reopens-work", "repeat-creates-an-owner", "timeout-abandons"],
)
def test_broken_callers_fail_the_new_histories(
    call: Callable[[PrimitiveOwner], object], history: str, defect: str
) -> None:
    scenario = caller_example(call)
    if call is creating_caller:
        # The first call's owner is the one whose outcome is observed.
        scenario = replace(scenario, observe=latest_created_value)
    with pytest.raises(AssertionError, match=defect):
        assert_synchronous_caller(primitive, scenario, history=history)


@pytest.mark.parametrize("history", ["repeat-same-value", "claim-held"])
def test_a_creating_caller_passes_with_its_same_owner_repeat(history: str) -> None:
    owners: list[PrimitiveOwner] = []

    def creating(owner: PrimitiveOwner) -> object:
        owners.append(PrimitiveOwner.objects.create())
        return deferring_caller(owners[-1])

    scenario = replace(
        caller_example(creating, repeat=lambda owner: deferring_caller(owners[-1])),
        observe=lambda: PrimitiveOwner.objects.get(pk=owners[-1].pk).value if owners else "before",
    )
    assert_synchronous_caller(primitive, scenario, history=history)


class EndpointBusy(Exception):
    """The entry point's own error for a change another worker is completing."""


def translating_caller(owner: PrimitiveOwner) -> object:
    # The entry point reports the held claim as its own error, raised from the library's report.
    try:
        return strict_caller(owner)
    except WorkIncomplete as incomplete:
        raise EndpointBusy("the change is already in progress") from incomplete


def detached_caller(owner: PrimitiveOwner) -> object:
    # Translates the report but deliberately detaches it, so nothing says the claim was held.
    try:
        return strict_caller(owner)
    except WorkIncomplete:
        raise EndpointBusy("the change is already in progress") from None


def unrelated_failure_caller(owner: PrimitiveOwner) -> object:
    # Swallows the report and fails for an unrelated reason.
    try:
        return strict_caller(owner)
    except WorkIncomplete:
        pass
    raise EndpointBusy("an unrelated refusal")


def test_a_held_claim_reported_through_the_entry_points_own_error_passes() -> None:
    scenario = caller_example(translating_caller, requires_inline=True)
    assert_synchronous_caller(primitive, scenario, history="claim-held")


@pytest.mark.parametrize("call", [detached_caller, unrelated_failure_caller], ids=["from-none", "unrelated"])
def test_an_error_not_raised_from_the_held_claim_still_fails(call: Callable[[PrimitiveOwner], object]) -> None:
    scenario = caller_example(call, requires_inline=True)
    with pytest.raises(EndpointBusy):
        assert_synchronous_caller(primitive, scenario, history="claim-held")


def test_response_lost_requires_its_binding() -> None:
    scenario = replace(caller_example(deferring_caller), lose_response=None)
    with pytest.raises(ObligationContractDesignError, match="lose_response"):
        assert_synchronous_caller(primitive, scenario, history="response-lost")


@pytest.fixture(name="registered_publication")
def registered_publication_fixture(publication: Publication) -> Iterator[Publication]:
    entry = DurableWorkRegistration(
        name="publication_caller", capability=publication, cadence_seconds=60, queue="celery"
    )
    with durable_work_installed(entry):
        yield publication


def operation_caller(publication: Publication) -> SynchronousCallerExample[str]:
    owner = PrimitiveOwner.objects.create()

    def call() -> object:
        with publication.atomic(owner.pk, notify_worker=False) as pending:
            work = pending.request_work(PublicationWork(payload="applied", universe="acme"))
        return publication.complete_or_defer(work)

    def applied_then_lost_operation(self: Publication, work: PublicationWork) -> ProviderResult:
        # EXTERNAL SEAM: the provider accepts the operation, then its response never arrives.
        Publication.calls.append(work.payload)
        raise TimeoutError("provider accepted; response lost")

    return SynchronousCallerExample(
        call=call,
        observe=lambda: PrimitiveOwner.objects.get(pk=owner.pk).value,
        expected="unknown",
        effect_calls=lambda: len(Publication.calls),
        fail_effect=nullcontext,
        failure=None,
        lose_response=lambda: patch.object(Publication, "perform_effect", applied_then_lost_operation),
    )


@pytest.mark.parametrize("resend", [False, True], ids=["evidence-blocks-resend", "rejects-blind-resend"])
def test_an_uncertain_operation_is_not_resent(
    registered_publication: Publication, monkeypatch: pytest.MonkeyPatch, resend: bool
) -> None:
    scenario = operation_caller(registered_publication)
    if resend:
        recover = Publication.recover

        def resending_recovery(self: Publication) -> int:
            result = recover(self)
            # Deliberate defect: recovery submits again although the outcome is unknown.
            for work in PublicationWork.objects.filter(state=DurableWorkState.UNKNOWN):
                Publication.calls.append(work.payload)
            return result

        monkeypatch.setattr(Publication, "recover", resending_recovery)
    with pytest.raises(AssertionError, match="blindly resent") if resend else nullcontext():
        assert_synchronous_caller(registered_publication, scenario, history="response-lost")
