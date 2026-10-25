"""Bind the tiny tutorial's actual arithmetic, product commands and printed output."""

from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass
from io import StringIO

from pytest_obligation.contract import NotApplicable
from pytest_obligation.profiles.catalog import ConvergenceFamily

from due.django.conformance import ProviderProbe, ProviderSeam, SupersessionExample, TerminalExample, seam_examples
from examples.numbers import effects
from examples.numbers.effects import calculate, request_number, retry_number
from examples.numbers.models import Calculation, CalculationWork


@dataclass
class Arithmetic:
    output: StringIO
    calls: ProviderProbe[[int], int, int]


@contextmanager
def environment() -> Iterator[Arithmetic]:
    with StringIO() as output:
        observed = Arithmetic(output, ProviderProbe(effects.double, key=lambda number: number))
        with redirect_stdout(output), observed.calls.patch(effects, "double"):
            yield observed


def product(work: CalculationWork) -> int | None:
    return Calculation.objects.get(pk=work.owner_id).result


def make_work() -> CalculationWork:
    return request_number(Calculation.objects.create().pk, 21)


def terminal() -> TerminalExample[CalculationWork]:
    def rearm(work: CalculationWork) -> None:
        assert work.owner_id is not None
        assert retry_number(work.owner_id)

    return TerminalExample(observe=lambda work: Calculation.objects.get(pk=work.owner_id).failed, rearm=rearm)


def snapshot() -> SupersessionExample[CalculationWork, int | None]:
    work = make_work()
    owner_id = work.owner_id
    assert owner_id is not None
    return SupersessionExample(
        work=work,
        change=lambda: request_number(owner_id, 7),
        observe=lambda: product(work),
        after_apply=True,
    )


# ARRANGE: create a calculation and call request_number with a captured input.
# REAL PRODUCTION: double sleeps/prints/calculates; guarded settlement saves the answer.
# EXTERNAL SEAM: observe the real stdout; ProviderProbe counts or rejects calls to double.
# OBSERVE: read Calculation.result independently; replay must print the actual answer twice.
SEAM = ProviderSeam(
    environment=environment,
    make_work=make_work,
    observe=lambda provider, work: product(work),
    delivered=lambda provider, work: work.number * 2,
    calls=lambda provider: provider.calls.total,
    calls_for=lambda provider, work: provider.calls.count(work.number),
    provider_state=lambda provider, work: provider.output.getvalue().strip().split("\n")[-1],
    provider_expected=lambda provider, work: f"double({work.number}) = {work.number * 2}",
    refuse=lambda provider: provider.calls.reject(lambda: OSError("calculation unavailable")),
)
PRODUCER = SEAM.producer(
    arrange=lambda provider: Calculation.objects.create(),
    produce=lambda provider, sample: (request_number(sample.pk, 21),),
    observe=lambda provider, sample: Calculation.objects.get(pk=sample.pk).result,
    expected=42,
)
ADMISSION = SEAM.producer(
    arrange=lambda provider: Calculation.objects.create(),
    produce=lambda provider, sample: (request_number(sample.pk, 21),),
    observe=lambda provider, sample: Calculation.objects.get(pk=sample.pk).number,
    expected=21,
)
EXAMPLES = seam_examples(
    calculate,
    SEAM,
    fixtures=("number_provider",),
    environment=environment,
    terminal=terminal,
    snapshot=snapshot,
    producers={"request number": PRODUCER},
    admission_failures={"partial request": SEAM.interrupted(ADMISSION, CalculationWork)},
    convergence_families={
        family: NotApplicable("Arithmetic has no mutable remote state; only the fenced product result is published.")
        for family in ConvergenceFamily
        if family.name != "STALE_SNAPSHOTS"
    },
)
