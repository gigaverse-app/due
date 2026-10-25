"""Run the tutorial's actual histories and independently read the product result."""

import pytest

from due.django.conformance import hold_nudges
from due.django.public import DurableWorkState
from examples.numbers import lesson
from examples.numbers.effects import request_number
from examples.numbers.models import Calculation, CalculationWork

pytestmark = pytest.mark.django_db(transaction=True)


def test_rolled_back_request_changes_neither_product_nor_work():
    sample = lesson.rollback()
    saved = Calculation.objects.get(pk=sample.pk)
    assert saved.number == 0
    assert saved.result is None
    assert not CalculationWork.objects.filter(owner=saved).exists()


def test_repeating_current_intent_preserves_its_published_result():
    sample = lesson.lost_notification()
    work = CalculationWork.objects.get(owner=sample)
    with hold_nudges():
        repeated = request_number(sample.pk, 21)
    sample.refresh_from_db()
    assert sample.result == 42
    assert repeated.pk == work.pk
    assert repeated.desired_revision == work.desired_revision
    assert repeated.state == DurableWorkState.SUCCEEDED


@pytest.mark.parametrize(
    ("run", "number", "answer", "printed"),
    [
        (lesson.lost_notification, 21, 42, ["double(21) = 42"]),
        (lesson.interrupted_attempt, 21, 42, ["double(21) = 42", "double(21) = 42"]),
        (lesson.stale_result, 7, 14, ["double(21) = 42", "double(7) = 14"]),
    ],
    ids=["lost-notification", "interrupted-after-effect", "old-result-after-new-intent"],
)
def test_lesson_recovers_the_requested_result(run, number, answer, printed, capsys):
    sample = run()
    saved = Calculation.objects.get(pk=sample.pk)
    work = CalculationWork.objects.get(owner=saved)
    assert (saved.number, saved.result, saved.failed) == (number, answer, False)
    assert work.state == DurableWorkState.SUCCEEDED
    assert work.applied_revision == work.desired_revision
    assert [line for line in capsys.readouterr().out.splitlines() if line.startswith("double(")] == printed
