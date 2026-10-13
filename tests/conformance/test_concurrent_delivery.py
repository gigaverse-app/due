"""
The shared race proof rejects omitted effects, duplicate effects and lost product settlement.

Design and usage: docs/architecture.md#verification.
"""

from collections.abc import Callable, Iterator
from contextlib import nullcontext
from functools import partial
from unittest.mock import patch

import pytest

from due.django.conformance.suite import (
    DeliveryExample,
    EffectExamples,
    assert_two_workers_one_settlement,
    failing_external_call,
)
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

type Observation = tuple[str, int]


def delivery() -> DeliveryExample[PrimitiveWork, Observation]:
    work = record()
    return DeliveryExample(
        work,
        lambda: (PrimitiveOwner.objects.get(pk=work.owner_id).value, len(PrimitiveEffect.calls)),
        (work.payload, 1),
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("fault", [None, "omit_provider", "duplicate_provider", "omit_product"])
def test_competing_delivery_proof_has_independent_product_and_provider_observations(fault: str | None) -> None:
    examples = EffectExamples(
        make_work=record,
        delivery=delivery,
        retry=partial(failing_external_call, "tests.harness.primitive.call"),
    )
    original = PrimitiveEffect.perform_effect

    def duplicate(self: PrimitiveEffect, work: PrimitiveWork) -> str:
        original(self, work)
        return original(self, work)

    mutations = {
        "omit_provider": patch.object(PrimitiveEffect, "perform_effect", lambda self, work: work.payload),
        "duplicate_provider": patch.object(PrimitiveEffect, "perform_effect", duplicate),
        "omit_product": patch.object(PrimitiveEffect, "finalize_success", lambda self, work, result: None),
    }
    with (
        mutations[fault] if fault else nullcontext(),
        pytest.raises(AssertionError, match="required application outcome") if fault else nullcontext(),
    ):
        assert_two_workers_one_settlement(primitive, examples)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("fault", [None, "drop_continuation", "invent_success"])
def test_competing_deliveries_preserve_unfinished_continuations(fault: str | None) -> None:
    from datetime import timedelta

    from due.django.conformance.concurrency import assert_competing_deliveries
    from due.django.public import ContinueAfter, complete_work
    from due.django.runtime.coordination import WorkClaim, WorkExecutionContext

    work = record()
    primitive.enqueue_execution(work.pk)

    def wait(
        self: PrimitiveEffect, value: PrimitiveWork, context: WorkExecutionContext[PrimitiveWork]
    ) -> ContinueAfter:
        with context.external_call():
            self.perform_effect(value)
        return ContinueAfter(timedelta(minutes=1), until=None)

    def invent_success(
        claim: WorkClaim[PrimitiveWork],
        continuation: ContinueAfter,
        *,
        finalize_expired: Callable[[PrimitiveWork], None] | None = None,
    ) -> bool:
        return complete_work(claim)

    mutations = {
        "drop_continuation": patch("due.django.runtime.coordination.continue_work", return_value=True),
        "invent_success": patch("due.django.runtime.coordination.continue_work", invent_success),
    }
    messages = {"drop_continuation": "release the winning claim", "invent_success": "must remain owed"}
    with (
        patch.object(PrimitiveEffect, "apply", wait),
        mutations[fault] if fault else nullcontext(),
        pytest.raises(AssertionError, match=messages[fault]) if fault else nullcontext(),
    ):
        assert_competing_deliveries(
            primitive,
            work,
            observe=lambda: (PrimitiveOwner.objects.get(pk=work.owner_id).value, len(PrimitiveEffect.calls)),
            expected=("before", 1),
        )


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("option", ["time_limit", "soft_time_limit"])
def test_producer_loss_proof_rejects_drifted_worker_execution_limits(option: str) -> None:
    from dataclasses import replace
    from uuid import UUID

    from due.django.conformance.suite import (
        assert_lost_notification_reaches_domain_outcome,
    )
    from due.django.runtime.registry import installed
    from due.django.runtime.tasks import run_durable_work_task

    entry = installed.get()["primitive_self_test"]

    def drifted_delivery(work_id: int, token: UUID) -> None:
        options = {"time_limit": entry.execution_time_limit, "soft_time_limit": entry.execution_soft_time_limit}
        options[option] += 1
        run_durable_work_task.apply_async(args=(entry.name, work_id, str(token)), queue=entry.queue, **options)

    changed = replace(primitive, nudge=drifted_delivery)
    examples = EffectExamples(
        make_work=record,
        delivery=delivery,
        retry=partial(failing_external_call, "tests.harness.primitive.call"),
    )
    with (
        patch.dict(installed.get(), {entry.name: replace(entry, capability=changed)}),
        pytest.raises(AssertionError, match=option),
    ):
        assert_lost_notification_reaches_domain_outcome(changed, examples)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("rollback", [False, True])
@pytest.mark.parametrize("fault", [None, "early_notification", "early_provider", "omit_product"])
def test_shared_producer_scenario_rejects_broken_application_wiring(
    rollback: bool, fault: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from due.django.conformance.scenarios import (
        ProducerExample,
        assert_producer_transaction,
    )
    from due.django.runtime.tasks import run_durable_work_task

    owner = PrimitiveOwner.objects.create()

    def produce() -> tuple[PrimitiveWork, ...]:
        work = record(owner=owner, notify_worker=True)
        if fault == "early_notification":
            run_durable_work_task.apply_async(args=("primitive_self_test", work.pk, "premature"))
        if fault == "early_provider":
            primitive.perform_effect(work)
        return (work,)

    if fault == "omit_product":
        monkeypatch.setattr(PrimitiveEffect, "finalize_success", lambda self, work, result: None)
    example = ProducerExample(
        produce=produce,
        observe=lambda: (PrimitiveOwner.objects.get(pk=owner.pk).value, len(PrimitiveEffect.calls)),
        expected=("after", 1),
        effect_calls=lambda: len(PrimitiveEffect.calls),
    )
    messages = {
        "early_notification": "notification escaped",
        "early_provider": "provider I/O escaped",
        "omit_product": "required product outcome",
    }
    detects_fault = fault is not None and not (rollback and fault == "omit_product")
    with pytest.raises(AssertionError, match=messages[fault]) if fault is not None and detects_fault else nullcontext():
        assert_producer_transaction(primitive, example, rollback=rollback)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("rollback", [False, True])
@pytest.mark.parametrize("fault", [None, "unchanged", "unstored_revision"])
def test_shared_producer_scenario_proves_revisions_without_accepting_unchanged_work(
    rollback: bool, fault: str | None
) -> None:
    from due.django.conformance.scenarios import (
        ProducerExample,
        assert_producer_transaction,
    )

    owner = PrimitiveOwner.objects.create()
    original = record(payload="original", owner=owner)

    def produce() -> tuple[PrimitiveWork, ...]:
        work = record(payload="original" if fault else "revised", owner=owner, notify_worker=True)
        if fault == "unstored_revision":
            work.desired_revision += 1
        PrimitiveOwner.objects.filter(pk=owner.pk).update(value="accepted")
        return (work,)

    example = ProducerExample(
        produce=produce,
        observe=lambda: (PrimitiveOwner.objects.get(pk=owner.pk).value, tuple(PrimitiveEffect.calls)),
        expected=("revised", ("revised",)),
        effect_calls=lambda: len(PrimitiveEffect.calls),
    )
    with pytest.raises(AssertionError, match="advance its revision") if fault else nullcontext():
        assert_producer_transaction(primitive, example, rollback=rollback)
    if rollback and not fault:
        original.refresh_from_db()
        assert original.payload == "original"
        assert original.desired_revision == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("rollback", [False, True])
@pytest.mark.parametrize("fault", [None, "early_provider", "early_notification", "omit_product", "drop_work"])
def test_admission_proof_does_not_require_provider_readiness(rollback: bool, fault: str | None) -> None:
    from due.django.conformance.scenarios import (
        ProducerExample,
        assert_producer_transaction,
    )
    from due.django.runtime.tasks import run_durable_work_task

    owner = PrimitiveOwner.objects.create()

    def produce() -> tuple[PrimitiveWork, ...]:
        work = record(owner=owner, notify_worker=True)
        # Test-only broken producers exercise the shared assertions, not adopter bindings.
        if fault != "omit_product":
            PrimitiveOwner.objects.filter(pk=owner.pk).update(value="accepted")
        if fault == "early_provider":
            primitive.perform_effect(work)
        if fault == "early_notification":
            run_durable_work_task.apply_async(args=("primitive_self_test", work.pk, "premature"))
        if fault == "drop_work":
            PrimitiveWork.objects.filter(pk=work.pk).delete()
        return (work,)

    example = ProducerExample(
        produce=produce,
        observe=lambda: PrimitiveOwner.objects.get(pk=owner.pk).value,
        expected="accepted",
        effect_calls=lambda: len(PrimitiveEffect.calls),
    )
    messages = {
        "early_provider": "provider I/O escaped",
        "early_notification": "notification escaped",
        "omit_product": "committed admission",
        "drop_work": "committed obligations",
    }
    detects_fault = fault is not None and (not rollback or fault in {"early_provider", "early_notification"})
    with pytest.raises(AssertionError, match=messages[fault]) if fault is not None and detects_fault else nullcontext():
        assert_producer_transaction(primitive, example, rollback=rollback, recover=False)
    assert not PrimitiveEffect.calls or fault == "early_provider"


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize(
    "fault",
    [
        None,
        "no_transaction",
        "no_progress",
        "early_provider",
        "early_notification",
        "outer_transaction",
        "fault_cleanup",
        "suppressed_failure",
    ],
)
@pytest.mark.parametrize("interrupt", [False, True])
def test_standalone_admission_failure_proof_detects_leaks(fault: str | None, interrupt: bool) -> None:
    from contextlib import contextmanager

    from django.db import transaction

    from due.django.conformance.scenarios import (
        AdmissionFailureExample,
        ProducerExample,
        assert_admission_failure,
    )
    from due.django.runtime.tasks import run_durable_work_task

    owner = PrimitiveOwner.objects.create()
    checkpoint: Callable[[], None] | None = None

    class Interrupted(Exception):
        pass

    @contextmanager
    def during(check: Callable[[], None]) -> Iterator[None]:
        nonlocal checkpoint
        checkpoint = check
        try:
            with transaction.atomic() if fault == "outer_transaction" else nullcontext():
                yield
        except AssertionError:
            if fault != "suppressed_failure":
                raise
        finally:
            checkpoint = None
            if fault == "fault_cleanup":
                PrimitiveWork.objects.filter(owner=owner).delete()
                PrimitiveOwner.objects.filter(pk=owner.pk).update(value=owner.value)

    def produce() -> tuple[PrimitiveWork, ...]:
        # Deliberately broken root controls, never an adopter's implementation.
        with (
            nullcontext()
            if fault in {"no_transaction", "fault_cleanup", "suppressed_failure"}
            else transaction.atomic()
        ):
            if checkpoint and fault == "no_progress":
                checkpoint()
                raise Interrupted()
            work = record(owner=owner, notify_worker=fault not in {"fault_cleanup", "suppressed_failure"})
            PrimitiveOwner.objects.filter(pk=owner.pk).update(value="accepted")
            if fault == "early_provider":
                primitive.perform_effect(work)
            if fault == "early_notification":
                run_durable_work_task.apply_async(args=("primitive_self_test", work.pk, "premature"))
            if checkpoint:
                checkpoint()
                raise Interrupted()
            return (work,)

    scenario = AdmissionFailureExample(
        admission=ProducerExample(
            produce,
            lambda: {"value": PrimitiveOwner.objects.get(pk=owner.pk).value},
            {"value": "accepted"},
            lambda: len(PrimitiveEffect.calls),
        ),
        during=during,
        expected_error=Interrupted,
    )
    messages = {
        "no_transaction": "leaked or deleted obligations",
        "no_progress": "partial",
        "outer_transaction": "fault fixture must not supply",
        "fault_cleanup": "leaked or deleted obligations",
        "suppressed_failure": "leaked or deleted obligations",
        "early_provider": "external effect",
        "early_notification": "wakeup",
    }
    detects = fault in {"early_provider", "early_notification"} or interrupt and fault is not None
    with pytest.raises(AssertionError, match=messages[fault]) if fault is not None and detects else nullcontext():
        assert_admission_failure(primitive, scenario, interrupt=interrupt)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("fault", [None, "revision_survives", "untouched"])
@pytest.mark.parametrize("interrupt", [False, True])
def test_admission_failure_proof_covers_revising_an_existing_row(fault: str | None, interrupt: bool) -> None:
    # A projection admits by revising its one row: the proof must see that
    # revision as partial admission, and its rollback as the row's old values.
    from contextlib import contextmanager

    from django.db import transaction

    from due.django.conformance.scenarios import (
        AdmissionFailureExample,
        ProducerExample,
        assert_admission_failure,
    )

    owner = PrimitiveOwner.objects.create()
    existing = record("before", owner=owner)
    checkpoint: Callable[[], None] | None = None

    class Interrupted(Exception):
        pass

    @contextmanager
    def during(check: Callable[[], None]) -> Iterator[None]:
        nonlocal checkpoint
        checkpoint = check
        try:
            yield
        finally:
            checkpoint = None

    def produce() -> tuple[PrimitiveWork, ...]:
        # Deliberately broken root controls, never an adopter's implementation.
        with nullcontext() if fault == "revision_survives" else transaction.atomic():
            PrimitiveOwner.objects.filter(pk=owner.pk).update(value="accepted")
            work = existing if fault == "untouched" else record("after", owner=owner)
            if checkpoint:
                checkpoint()
                raise Interrupted()
            return (work,)

    scenario = AdmissionFailureExample(
        admission=ProducerExample(
            produce,
            lambda: {"value": PrimitiveOwner.objects.get(pk=owner.pk).value},
            {"value": "accepted"},
            lambda: len(PrimitiveEffect.calls),
        ),
        during=during,
        expected_error=Interrupted,
    )
    # pytest-obligation's admission oracle, over (row, revision) obligation identities.
    messages = {
        ("revision_survives", True): "interrupted admission leaked or deleted obligations",
        ("untouched", True): "fault did not reach partial obligation writes",
        ("untouched", False): "admission reused preexisting obligations",
    }
    expected = messages.get((fault, interrupt)) if fault is not None else None
    with pytest.raises(AssertionError, match=expected) if expected else nullcontext():
        assert_admission_failure(primitive, scenario, interrupt=interrupt)
