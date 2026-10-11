"""Factory scope contract shared by application conformance examples.

``Binding[T]`` is a zero-argument factory returning ``T`` or a context manager
that yields ``T``. Use the latter for provider patches and resources that must
survive fault injection, recovery and observation. ``entered_binding`` executes
that contract once and guarantees context cleanup on failure.
"""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

type Binding[T] = Callable[[], T | AbstractContextManager[T]]


@contextmanager
def entered_binding[T](factory: Binding[T]) -> Iterator[T]:
    """Enter one factory result and keep its resource scope alive until the proof exits."""
    built = factory()
    if isinstance(built, AbstractContextManager):
        with built as value:
            yield value
    else:
        yield built
