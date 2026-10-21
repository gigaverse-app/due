"""
Self-test the same exhaustion proof required of bounded application suites.

Test-owned rows exercise the production runtime. Negative controls show that
the proof detects both invisible product failure and a nonfunctional re-arm.
Design and usage: docs/architecture.md#verification.
"""

from contextlib import nullcontext
from functools import partial
from unittest.mock import patch

import pytest

from due.django.conformance.suite import (
    DeliveryExample,
    EffectExamples,
    TerminalExample,
    assert_terminal_failure_is_visible_and_rearmable,
    failing_external_call,
)
from due.django.public import DurableWorkState
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


def observe(work: PrimitiveWork) -> str:
    return PrimitiveOwner.objects.get(pk=work.owner_id).value


def rearm(work: PrimitiveWork, *, retry_stopped: bool) -> None:
    assert work.owner_id is not None
    with primitive.atomic(work.owner_id) as pending:
        if retry_stopped:
            pending.request_work(PrimitiveWork(payload=work.payload, universe=work.universe), retry_stopped=True)
        else:
            assert pending.retry_failed()


def unfenced_rearm(work: PrimitiveWork) -> None:
    PrimitiveWork.objects.filter(pk=work.pk).update(state=DurableWorkState.READY, rearm_count=0, completed_at=None)


def delivery() -> DeliveryExample:
    work = record()
    return DeliveryExample(work, partial(observe, work), work.payload)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("fault", [None, "invisible_failure", "broken_rearm", "unfenced_rearm"])
@pytest.mark.parametrize("retry_stopped", [False, True])
def test_root_terminal_proof_runs_and_detects_missing_application_transitions(fault, retry_stopped):
    rearm_work = (
        (lambda work: None)
        if fault == "broken_rearm"
        else unfenced_rearm
        if fault == "unfenced_rearm"
        else partial(rearm, retry_stopped=retry_stopped)
    )
    examples = EffectExamples(
        make_work=record,
        delivery=delivery,
        retry=partial(failing_external_call, "tests.harness.primitive.call"),
        terminal=lambda: TerminalExample(observe=observe, rearm=rearm_work),
    )
    expected = {
        "invisible_failure": "no product-visible consequence",
        "broken_rearm": "did not fence the old execution",
        "unfenced_rearm": "did not fence the old execution",
    }
    with (
        patch.object(PrimitiveEffect, "finalize_retry", lambda *args: None)
        if fault == "invisible_failure"
        else nullcontext(),
        pytest.raises(AssertionError, match=expected[fault]) if fault else nullcontext(),
    ):
        assert_terminal_failure_is_visible_and_rearmable(primitive, examples)
