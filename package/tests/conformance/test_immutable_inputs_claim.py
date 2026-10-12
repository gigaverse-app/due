"""
The immutable-input check refuses a capability that moves admitted inputs and accepts one that cannot.

Positive and negative controls for ``work_table/immutable_inputs.py``: the same
perturbation and admission paths run against a producer that re-derives its
input from the owner (it must fail) and one that keeps the first admitted input
(it must pass), plus each positive control and the declared-wake refusal.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest

from due.django.conformance._internal.immutable_inputs import (
    AdmissionPath,
    admission_paths,
    assert_immutable_inputs_hold,
)
from due.django.conformance.binding import entered_binding
from due.django.conformance.registrations import durable_work_installed
from due.django.conformance.scenarios import (
    AdmissionFailureExample,
    ProducerExample,
    SynchronousCallerExample,
)
from due.django.conformance.suite import EffectExamples
from due.django.public import DurableWorkPolicy
from due.django.runtime.registry import DurableWorkRegistration
from tests.harness import primitive as provider
from tests.harness.dependents import (
    WAKES_DEPENDENT,
    DependentWork,
    dependent,
    record_dependent,
)
from tests.harness.dependents import (
    dependent_tables_fixture as dependent_tables_fixture,
)
from tests.harness.primitive import (
    PrimitiveEffect,
    PrimitiveOwner,
    PrimitiveWork,
    capture,
    primitive,
)
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]

type Admit = Callable[[PrimitiveOwner], PrimitiveWork]


def never_entered(*args: object, **kwargs: object) -> Any:
    # ARRANGE: required by the declaration only; the immutable-input proof never enters it.
    raise AssertionError("the immutable-input proof must not enter this binding")


def rederives(owner: PrimitiveOwner) -> PrimitiveWork:
    """REAL PRODUCTION shape that moves inputs: every request reads the owner's current value."""
    with primitive.atomic(owner.pk, notify_worker=False) as pending:
        return pending.request_work(PrimitiveWork(payload=pending.locked_owner.value, universe="TapIn"))


def keeps_first(owner: PrimitiveOwner) -> PrimitiveWork:
    """REAL PRODUCTION shape that freezes inputs: a later request repeats the admitted input."""
    with primitive.atomic(owner.pk, notify_worker=False) as pending:
        existing = pending.existing_work
        payload = existing.payload if existing is not None else pending.locked_owner.value
        return pending.request_work(PrimitiveWork(payload=payload, universe="TapIn"))


def move_owner_value(work: PrimitiveWork) -> None:
    # ARRANGE: the fact the payload is derived from moves.
    PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="moved")


def bindings(admit: Admit, *, fresh_owner_each_call: bool = False) -> dict[str, Callable[[], Any]]:
    """One binding of each admission kind over the same production command."""

    def target() -> Callable[[], PrimitiveOwner]:
        owner = PrimitiveOwner.objects.create()
        return PrimitiveOwner.objects.create if fresh_owner_each_call else lambda: owner

    def producer_example() -> ProducerExample[PrimitiveWork, int]:
        owner = target()
        return ProducerExample(
            produce=lambda: (admit(owner()),),
            observe=lambda: PrimitiveWork.objects.count(),
            expected=1,
            effect_calls=lambda: len(PrimitiveEffect.calls),
        )

    @contextmanager
    def producer() -> Iterator[ProducerExample[PrimitiveWork, int]]:
        yield producer_example()

    @contextmanager
    def admission_failure() -> Iterator[AdmissionFailureExample[PrimitiveWork, int]]:
        yield AdmissionFailureExample.after_save(producer_example(), PrimitiveWork)

    @contextmanager
    def synchronous_caller() -> Iterator[SynchronousCallerExample[int]]:
        owner = target()
        yield SynchronousCallerExample(
            call=lambda: primitive.complete_or_defer(admit(owner())),
            observe=lambda: len(PrimitiveEffect.calls),
            expected=1,
            effect_calls=lambda: len(PrimitiveEffect.calls),
            # EXTERNAL SEAM: the provider refuses, so the accepted change stays owed.
            fail_effect=lambda: patch.object(provider, "call", side_effect=ConnectionError("provider refused")),
            failure=ConnectionError,
        )

    return {"producer": producer, "admission_failure": admission_failure, "synchronous_caller": synchronous_caller}


def examples(
    admit: Admit, *, perturb: Callable[[PrimitiveWork], object] = move_owner_value, fresh: bool = False
) -> EffectExamples[PrimitiveWork]:
    declared = bindings(admit, fresh_owner_each_call=fresh)
    return EffectExamples(
        make_work=lambda: admit(PrimitiveOwner.objects.create()),
        delivery=never_entered,
        retry=never_entered,
        immutable_inputs="The first admitted payload is the obligation's identity.",
        perturb_inputs=perturb,
        producers={"command": declared["producer"]},
        admissions={"command": declared["producer"]},
        admission_failures={"command": declared["admission_failure"]},
        synchronous_callers={"command": declared["synchronous_caller"]},
    )


def paths(declared: EffectExamples[PrimitiveWork]) -> dict[str, AdmissionPath]:
    return {path.kind: path for path in admission_paths(declared)}


KINDS = ("producer", "admission", "admission_failure", "synchronous_caller")


@pytest.mark.parametrize("kind", KINDS)
def test_inputs_frozen_at_admission_hold(kind: str) -> None:
    declared = examples(keeps_first)
    assert_immutable_inputs_hold(primitive, declared, paths(declared)[kind], name="primitive_self_test")


@pytest.mark.parametrize("kind", KINDS)
def test_inputs_rederived_on_every_request_are_refused(kind: str) -> None:
    declared = examples(rederives)
    with pytest.raises(AssertionError, match="immutable_inputs is false.*Bind the profile E families"):
        assert_immutable_inputs_hold(primitive, declared, paths(declared)[kind], name="primitive_self_test")


def test_a_command_that_wakes_in_flight_work_is_refused() -> None:
    def move_and_wake(work: PrimitiveWork) -> None:
        # REAL PRODUCTION: a fact-moving command tells existing work to re-read; the claimed revision is fenced.
        move_owner_value(work)
        primitive.wake(work.pk)

    declared = examples(keeps_first, perturb=move_and_wake)
    with pytest.raises(AssertionError, match="immutable_inputs is false"):
        assert_immutable_inputs_hold(primitive, declared, paths(declared)["producer"], name="primitive_self_test")


def test_a_perturbation_that_moves_nothing_is_refused() -> None:
    declared = examples(rederives, perturb=lambda work: None)
    with pytest.raises(AssertionError, match="perturb_inputs wrote no product row"):
        assert_immutable_inputs_hold(primitive, declared, paths(declared)["producer"], name="primitive_self_test")


def test_a_path_that_never_revisits_existing_work_is_refused() -> None:
    # The re-deriving producer would fail if revisited; a binding that admits a fresh owner each call never does.
    declared = examples(rederives, fresh=True)
    with pytest.raises(AssertionError, match="revisited nothing"):
        assert_immutable_inputs_hold(primitive, declared, paths(declared)["producer"], name="primitive_self_test")


@pytest.mark.usefixtures("dependent_tables")
@pytest.mark.parametrize("declared_wake", [False, True], ids=["control", "declared-wake"])
def test_a_declared_dependent_is_refused(declared_wake: bool) -> None:
    @contextmanager
    def admit_dependent() -> Iterator[ProducerExample[DependentWork, int]]:
        owner = PrimitiveOwner.objects.create()
        yield ProducerExample(
            produce=lambda: (record_dependent(owner),),
            observe=lambda: DependentWork.objects.count(),
            expected=1,
            effect_calls=lambda: 0,
        )

    declared = EffectExamples(
        make_work=lambda: record_dependent(PrimitiveOwner.objects.create()),
        delivery=never_entered,
        retry=never_entered,
        immutable_inputs="A dependent's universe is fixed at admission.",
        perturb_inputs=lambda work: PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="moved"),
        producers={"record": admit_dependent},
    )
    upstream = PrimitiveEffect.for_owner(
        owner=PrimitiveOwner,
        work=PrimitiveWork,
        nudge=capture,
        policy=DurableWorkPolicy(retry_delay=timedelta(0)),
        wakes=WAKES_DEPENDENT if declared_wake else (),
    )
    route = DurableWorkRegistration(name="primitive_self_test", capability=upstream, queue="celery")
    (path,) = admission_paths(declared)
    with durable_work_installed(route):
        if not declared_wake:
            # Positive control: the same dependent, with nothing declared to wake it, holds.
            assert_immutable_inputs_hold(dependent, declared, path, name="dependent_self_test")
            return
        with pytest.raises(AssertionError, match="declare.*as a Dependent"):
            assert_immutable_inputs_hold(dependent, declared, path, name="dependent_self_test")


def test_every_admission_kind_becomes_one_named_case() -> None:
    declared = replace(examples(keeps_first), admissions={"command": never_entered})
    assert [path.id for path in admission_paths(declared)] == [
        "producer-command",
        "admission-command",
        "admission_failure-command",
        "synchronous_caller-command",
    ]


def test_plain_example_factories_follow_the_public_binding_contract() -> None:
    declared = examples(keeps_first)
    with entered_binding(declared.producers["command"]) as example:
        path = AdmissionPath("producer", "plain", lambda: example)
        assert_immutable_inputs_hold(primitive, declared, path, name="primitive_self_test")


def test_explicit_revisit_must_reach_the_original_owner() -> None:
    from dataclasses import replace

    declared = examples(keeps_first)
    binding = declared.producers["command"]

    @contextmanager
    def inert_revisit():
        with entered_binding(binding) as example:
            yield replace(example, revisit=lambda works: None)

    declared = replace(declared, producers={"command": inert_revisit})
    with pytest.raises(AssertionError, match="revisit.*admitted owner"):
        assert_immutable_inputs_hold(primitive, declared, paths(declared)["producer"], name="control")
