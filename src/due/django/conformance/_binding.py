"""Adopter factories can return a value or own a context-managed fixture scope."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

type Binding[T] = Callable[[], T | AbstractContextManager[T]]


@contextmanager
def entered_binding[T](factory: Binding[T]) -> Iterator[T]:
    built = factory()
    if isinstance(built, AbstractContextManager):
        with built as value:
            yield value
    else:
        yield built
