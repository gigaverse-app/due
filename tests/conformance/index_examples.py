"""Shared bindings for applications using the same real external search provider."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass

from pytest_obligation.profiles.harmless_replay import ReplaySafeEffect

from due.django.conformance.suite import DeliveryExample, EffectExamples, FailureExample, TerminalExample
from due.django.public import DurableWorkCapability, UniverseDurableWork
from tests.examples.books_support import index_environment

type BookContent = tuple[str, str]


@dataclass(frozen=True)
class Stage[W: UniverseDurableWork, ResultT]:
    capability: DurableWorkCapability[W, ResultT]
    make_work: Callable[[], W]
    observe: Callable[[W], BookContent | None]
    external_observe: Callable[[W], BookContent | bool | None]
    effect_calls: Callable[[W], int]
    unavailable: Callable[[], AbstractContextManager[None]]
    execute: Callable[[W], object]
    terminal: TerminalExample[W]
    expected: BookContent


def examples_for[W: UniverseDurableWork, ResultT](stage: Stage[W, ResultT]) -> EffectExamples[W]:
    def delivery() -> DeliveryExample[W, BookContent | None]:
        work = stage.make_work()
        return DeliveryExample(work, lambda: stage.observe(work), stage.expected, lambda: stage.effect_calls(work))

    @contextmanager
    def retry() -> Iterator[FailureExample[W]]:
        with stage.unavailable():
            yield FailureExample(attempts=stage.effect_calls)

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
        immutable_inputs="The demo accepts immutable book submissions; editing or replacing a book is not exposed.",
        environment=index_environment,
    )
