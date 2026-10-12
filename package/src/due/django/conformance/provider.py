"""Typed, scoped observation and faults at a real synchronous provider boundary.

A probe records entries, not successful effects. Always retain an independent
provider/product oracle. It never claims, settles or repairs Due work. Patches
are process-local and belong in isolated tests, not running application servers.
"""

from collections import Counter
from collections.abc import Callable, Hashable, Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from inspect import getattr_static, iscoroutinefunction
from threading import Lock
from unittest.mock import patch


@dataclass(frozen=True)
class _Fault[KeyT]:
    error: Callable[[], Exception]
    after: bool
    when: Callable[[KeyT], bool]


@dataclass
class ProviderProbe[**P, ResultT, KeyT: Hashable]:
    """Observe the actual callable with its original argument and return types.

    ``key`` correlates provider arguments to a stable application identity.
    ``patch(owner, name)`` verifies the target before replacing it with this
    delegating callable. Fault scopes are context-local; nested scopes restore
    their predecessor. Normal provider calls and exceptions pass through.
    """

    call: Callable[P, ResultT]
    key: Callable[P, KeyT]
    _counts: Counter[KeyT] = field(default_factory=Counter, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _fault: ContextVar[_Fault[KeyT] | None] = field(
        default_factory=lambda: ContextVar("provider_probe_fault", default=None), init=False, repr=False
    )

    def __post_init__(self) -> None:
        if iscoroutinefunction(self.call) or iscoroutinefunction(type(self.call).__call__):
            raise TypeError("ProviderProbe requires a synchronous provider callable")

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> ResultT:
        identity = self.key(*args, **kwargs)
        with self._lock:
            self._counts[identity] += 1
        fault = self._fault.get()
        selected = fault is not None and fault.when(identity)
        if selected and fault is not None and not fault.after:
            raise fault.error()
        # The real effect must return before response loss can be injected.
        # A counter alone is never evidence that the provider committed anything.
        result = self.call(*args, **kwargs)
        if selected and fault is not None:
            raise fault.error()
        return result

    def count(self, identity: KeyT) -> int:
        """Actual entries for an identity, including rejected/failed calls."""
        with self._lock:
            return self._counts[identity]

    @property
    def total(self) -> int:
        with self._lock:
            return self._counts.total()

    @contextmanager
    def patch(self, owner: object, name: str) -> Iterator[ProviderProbe[P, ResultT, KeyT]]:
        # Reflection is confined to this explicit patch boundary. Refuse a typo
        # or another binding already replacing the provider the author supplied.
        if getattr(owner, name) != self.call:
            raise ValueError(f"provider target {name!r} is not the probe's original callable")
        descriptor = getattr_static(owner, name)
        if isinstance(descriptor, classmethod):
            raise TypeError("Patch a provider instance or module, not a classmethod")

        @wraps(self.call)
        def observed(*args: P.args, **kwargs: P.kwargs) -> ResultT:
            return self(*args, **kwargs)

        # A function descriptor preserves self for class-level SDK method seams.
        replacement = staticmethod(observed) if isinstance(descriptor, staticmethod) else observed
        with patch.object(owner, name, replacement):
            yield self

    @contextmanager
    def _inject(self, fault: _Fault[KeyT]) -> Iterator[None]:
        token = self._fault.set(fault)
        try:
            yield
        finally:
            self._fault.reset(token)

    def reject(
        self, error: Callable[[], Exception], *, when: Callable[[KeyT], bool] = lambda key: True
    ) -> AbstractContextManager[None]:
        """Raise before invoking the provider; no external effect is attempted."""
        return self._inject(_Fault(error=error, after=False, when=when))

    def lose_response(
        self, error: Callable[[], Exception], *, when: Callable[[KeyT], bool] = lambda key: True
    ) -> AbstractContextManager[None]:
        """Invoke the provider unchanged, then raise instead of returning its result."""
        return self._inject(_Fault(error=error, after=True, when=when))
