"""Common enrollment defaults for real applications using the search provider."""

from due.django.conformance.seams import ProviderSeam, seam_examples
from due.django.conformance.suite import EffectExamples, TerminalExample
from due.django.public import DurableWorkCapability, UniverseDurableWork
from integration_tests.examples.books_support import IndexCalls, index_environment

type BookContent = tuple[str, str]


def examples_for[W: UniverseDurableWork, ResultT](
    capability: DurableWorkCapability[W, ResultT],
    seam: ProviderSeam[W, IndexCalls, BookContent | None],
    terminal: TerminalExample[W],
) -> EffectExamples[W]:
    return seam_examples(
        capability,
        seam,
        fixtures=("book_index",),
        terminal=lambda: terminal,
        environment=index_environment,
    )
