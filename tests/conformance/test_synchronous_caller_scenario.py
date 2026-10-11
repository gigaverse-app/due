"""The shared synchronous-caller histories accept correct callers and catch broken ones."""

from collections.abc import Callable
from unittest.mock import patch

import pytest
from django.db import connection, transaction

from due.django.conformance.scenarios import (
    SYNCHRONOUS_CALLER_HISTORIES,
    SynchronousCallerExample,
    assert_synchronous_caller,
)
from due.django.public import CompletionErrors
from tests.harness import primitive as example
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

type Observation = tuple[str, int]


def caller_example(
    call: Callable[[PrimitiveOwner], object],
    *,
    failure: type[Exception] | None = ConnectionError,
    requires_inline: bool = False,
) -> SynchronousCallerExample[Observation]:
    owner = PrimitiveOwner.objects.create()
    return SynchronousCallerExample(
        call=lambda: call(owner),
        observe=lambda: (PrimitiveOwner.objects.get(pk=owner.pk).value, len(PrimitiveEffect.calls)),
        expected=("after", 1),
        effect_calls=lambda: len(PrimitiveEffect.calls),
        # EXTERNAL SEAM: the provider boundary refuses; the library classifies it.
        fail_effect=lambda: patch.object(example, "call", side_effect=ConnectionError("provider refused")),
        failure=failure,
        requires_inline=requires_inline,
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
