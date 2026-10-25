"""Tiny application callbacks; Due owns admission, claims, recovery and settlement."""

from time import sleep

from due.django.public import DurableWorkState, ReplaySafeEffect
from examples.numbers.models import Calculation, CalculationWork


def double(number: int) -> int:
    sleep(0.05)
    answer = number * 2
    print(f"double({number}) = {answer}", flush=True)
    return answer


class DoubleNumber(ReplaySafeEffect[Calculation, CalculationWork, int]):
    def perform_effect(self, work: CalculationWork) -> int:
        return double(work.number)

    def finalize_success(self, work: CalculationWork, result: int) -> None:
        # Due checks the claim and requested revision while holding the locks.
        # Only this guarded callback publishes the answer to the product row.
        Calculation.objects.filter(pk=work.owner_id).update(result=result, failed=False)

    def finalize_retry(self, work: CalculationWork, error: Exception) -> None:
        if work.state == DurableWorkState.EXHAUSTED:
            Calculation.objects.filter(pk=work.owner_id).update(failed=True)


calculate = DoubleNumber.for_owner(work=CalculationWork)


def request_number(calculation_id: int, number: int) -> CalculationWork:
    with calculate.atomic(calculation_id) as pending:
        sample = pending.locked_owner
        # Repeating current intent reuses its work row, including a completed one.
        # Clear the published result only when the desired input actually changes.
        if sample.number != number:
            sample.number = number
            sample.result = None
            sample.failed = False
            sample.save(update_fields=["number", "result", "failed"])
        return pending.request_work(CalculationWork(number=number))


def retry_number(calculation_id: int) -> bool:
    with calculate.atomic(calculation_id) as pending:
        retried = pending.retry_failed()
        if retried:
            Calculation.objects.filter(pk=calculation_id).update(failed=False)
        return retried
