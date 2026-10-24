"""Executable tutorial histories. Transport/time faults are local to this process.

Run with `python -m django shell --settings=examples.settings
-c 'from examples.numbers.lesson import run; run()'`.
Do not run independent workers against the same database during these controlled
histories. The separate live lesson uses ordinary workers and Beat instead.
"""

from unittest.mock import patch

from django.db import transaction

from due.django.conformance import WorkerDied, WorkTableDelivery, applied_snapshot, die_after, hold_nudges
from due.django.public import DurableWorkState
from examples.numbers import effects
from examples.numbers.effects import calculate, request_number
from examples.numbers.models import Calculation, CalculationWork


def rollback() -> Calculation:
    sample = Calculation.objects.create()
    with hold_nudges() as sent:
        try:
            with transaction.atomic():
                request_number(sample.pk, 21)
                raise ValueError("the rest of the application request failed")
        except ValueError:
            pass
    sample.refresh_from_db()
    assert sample.number == 0 and sample.result is None
    assert not CalculationWork.objects.filter(owner=sample).exists()
    assert not sent
    print("Rollback: number=0, result=None, no work row, no notification")
    return sample


def lost_notification() -> Calculation:
    sample = Calculation.objects.create()
    with hold_nudges() as dropped:
        work = request_number(sample.pk, 21)
    assert len(dropped) == 1
    assert CalculationWork.objects.get(pk=work.pk).state == DurableWorkState.READY
    assert Calculation.objects.get(pk=sample.pk).result is None
    print("Notification lost: work is still ready, result=None")
    with WorkTableDelivery(calculate).session() as delivery:
        delivery.recover()
    sample.refresh_from_db()
    assert sample.result == 42
    print("Recovery: result=42, without requesting the calculation again")
    return sample


def interrupted_attempt() -> Calculation:
    sample = Calculation.objects.create()
    with hold_nudges():
        work = request_number(sample.pk, 21)
    with patch.object(effects, "double", die_after(effects.double)):
        try:
            calculate.attempt_execution(work.pk)
        except WorkerDied:
            pass
        else:
            raise AssertionError("the worker-stop fault was never reached")
    assert Calculation.objects.get(pk=sample.pk).result is None
    assert CalculationWork.objects.get(pk=work.pk).state == DurableWorkState.CLAIMED
    print("Attempt stopped after double returned: result=None, claim is still held")
    with WorkTableDelivery(calculate).session() as delivery:
        delivery.recover()
    sample.refresh_from_db()
    assert sample.result == 42
    print("Expired claim recovered: double ran again, result=42")
    return sample


def stale_result() -> Calculation:
    sample = Calculation.objects.create()
    with hold_nudges():
        work = request_number(sample.pk, 21)
        with applied_snapshot(
            calculate,
            work,
            change=lambda: request_number(sample.pk, 7),
            observe=lambda: Calculation.objects.get(pk=sample.pk).result,
        ) as race:
            old_attempt = race.take_snapshot()
            race.supersede()
            race.run_with_snapshot(old_attempt)
        sample.refresh_from_db()
        assert sample.number == 7 and sample.result is None
        print("Old attempt returned 42 after number changed to 7: result=None")
    with WorkTableDelivery(calculate).session() as delivery:
        delivery.recover()
    sample.refresh_from_db()
    assert sample.result == 14
    print("Current revision completed: number=7, result=14")
    return sample


def run() -> None:
    for lesson in (rollback, lost_notification, interrupted_attempt, stale_result):
        print(f"\n{lesson.__name__}")
        lesson()
