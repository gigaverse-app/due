"""Shared bindings for applications using the same real external search provider."""

from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from unittest.mock import patch

from pytest_obligation.profiles.harmless_replay import ReplaySafeEffect

from due.django.conformance.suite import DeliveryExample, EffectExamples, FailureExample, TerminalExample
from due.django.public import DurableWorkCapability, UniverseDurableWork
from examples.books import search
from tests.examples.books_support import index_environment

type BookContent = tuple[str, str]


@dataclass(frozen=True)
class Stage[W: UniverseDurableWork, ResultT]:
    capability: DurableWorkCapability[W, ResultT]
    make_work: Callable[[], W]
    key: Callable[[W], str]
    observe: Callable[[W], BookContent | None]
    external_observe: Callable[[W], BookContent | bool | None]
    effect_calls: Callable[[W], int]
    provider_method: str
    execute: Callable[[W], object]
    terminal: TerminalExample[W]
    expected: BookContent


def examples_for[W: UniverseDurableWork, ResultT](stage: Stage[W, ResultT]) -> EffectExamples[W]:
    def delivery() -> DeliveryExample[W, BookContent | None]:
        work = stage.make_work()
        return DeliveryExample(work, lambda: stage.observe(work), stage.expected, lambda: stage.effect_calls(work))

    @contextmanager
    def retry() -> Iterator[FailureExample[W]]:
        attempts: Counter[str] = Counter()

        def fail(value: search.IndexEntry | str) -> str:
            attempts[value.key if isinstance(value, search.IndexEntry) else value] += 1
            raise OSError("search provider unavailable")

        with patch.object(search, stage.provider_method, fail):
            yield FailureExample(attempts=lambda work: attempts[stage.key(work)])

    def replay() -> ReplaySafeEffect:
        # ARRANGE: admit through the real book command and, for publication, its predecessor.
        # REAL PRODUCTION: execute the stage's actual provider operation twice.
        # EXTERNAL SEAM: index_environment records SQLite writes without replacing them.
        # OBSERVE: read the independently committed search entry or activation bit.
        return ReplaySafeEffect(
            name=stage.capability.model._meta.label,
            prepare=stage.make_work,
            execute=stage.execute,
            observe=stage.external_observe,
            execution_count_for=stage.effect_calls,
        )

    return EffectExamples(
        fixtures=("book_index",),
        make_work=stage.make_work,
        delivery=delivery,
        retry=retry,
        terminal=lambda: stage.terminal,
        replay=replay,
        convergence_not_assessed="Book creation admits a fresh owner; the existing admission bindings do not revisit admitted work to prove input immutability.",
        environment=index_environment,
    )
