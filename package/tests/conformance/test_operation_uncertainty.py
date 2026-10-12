"""Shared uncertainty proofs reject product omission, duplicate I/O and lost budgets."""

from asyncio import CancelledError
from contextlib import nullcontext
from dataclasses import replace

import pytest
from django.db.models import F

from due.django.conformance.scenarios import assert_uncertainty_blocks_resend
from due.django.testing import WorkerDied
from tests.harness.operation import (
    Publication,
    delivery_example,
    record,
)
from tests.harness.operation import operation_environment as operation_environment
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("max_rearms", [0, 3])
@pytest.mark.parametrize("fault", [None, "omit_product", "repeat_io", "spend_budget"])
@pytest.mark.parametrize("interruption", [WorkerDied, CancelledError], ids=["worker-death", "cancelled"])
def test_uncertainty_proof_detects_broken_recovery(publication, monkeypatch, max_rearms, fault, interruption):
    publication = replace(publication, policy=replace(publication.policy, max_rearms=max_rearms))
    work = record(publication)

    def lose_receipt(self, attempt, result):
        raise interruption("provider accepted before receipt commit")

    monkeypatch.setattr(Publication, "record_applied_effect_evidence", lose_receipt)
    if fault == "omit_product":
        monkeypatch.setattr(Publication, "_finalize_unknown", lambda self, current: None)
    elif fault is not None:
        recover = publication.recover

        def broken_recovery(self):
            result = recover()
            if fault == "repeat_io":
                publication.perform_effect(work)
            else:
                # Deliberate broken implementation: uncertainty must not spend budget.
                type(work).objects.filter(pk=work.pk).update(rearm_count=F("rearm_count") + 1)
            return result

        monkeypatch.setattr(Publication, "recover", broken_recovery)
    example = delivery_example(work, expected="unknown")
    failure = {
        "omit_product": "product outcome",
        "repeat_io": "repeated provider I/O",
        "spend_budget": "spent the retry budget",
    }
    with pytest.raises(AssertionError, match=failure[fault]) if fault else nullcontext():
        assert_uncertainty_blocks_resend(publication, example)
