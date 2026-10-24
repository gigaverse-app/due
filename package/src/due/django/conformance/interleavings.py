"""Due's broker, clock and provider adapters. Portable scheduling lives elsewhere."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial

import pytest
from asgiref.sync import AsyncSingleThreadContext
from pytest_obligation.interleavings import ProviderControl

from due.django.conformance.background import (
    BackgroundWork,
    accounted_background_work,
    frozen_scheduler_clock,
)
from due.django.conformance.worker_connection import database_worker_connection
from due.django.models import UniverseDurableWork
from due.django.runtime.registry import registration_for


@dataclass
class BrokerControl:
    """
    Mutable broker behavior over the canonical BackgroundWork capture/dispatch owner.

    A dataclass retains the injected owner by identity; this controller toggles live
    transport state rather than validating a second representation of that state.
    """

    background: BackgroundWork
    """Per-history owner of captured messages and accounted worker/provider failures."""
    dropping: bool = False
    """Whether notifications are discarded while durable recovery remains available."""

    def drop(self, enabled: bool) -> None:
        """Discard held notifications and toggle loss of subsequent publications."""
        self.background.held.clear()
        self.dropping = enabled
        self.background.holding = enabled

    @contextmanager
    def admission(self) -> Iterator[None]:
        """Capture publications during admission; discard them if notification loss is enabled."""
        self.background.hold()
        try:
            yield
        finally:
            if self.dropping:
                self.background.held.clear()
            self.background.holding = self.dropping

    def start(self) -> None:
        """Release held production messages, or discard them under notification loss."""
        if self.dropping:
            self.background.held.clear()
        else:
            self.background.release()

    def redeliver(self, index: int) -> None:
        """Repeat the original captured worker delivery without reconstructing its payload."""
        assert 0 <= index < len(self.background.executed), "no actual worker delivery was captured"
        delivery = self.background.executed[index]
        self.background.redeliver(delivery)


@contextmanager
def environment() -> Iterator[tuple[BackgroundWork, pytest.MonkeyPatch]]:
    """Own patches, async bridge, frozen clock and accounted background work for one history."""
    with pytest.MonkeyPatch.context() as patches, AsyncSingleThreadContext(), frozen_scheduler_clock() as clock:
        with accounted_background_work(patches, clock) as background:
            yield background, patches


# Ordered actors already own fresh threads. Reuse the shared SQL-deadline and
# connection-cleanup owner instead of maintaining a second teardown protocol.
connection_scope = partial(database_worker_connection, statement_timeout_ms=5_000)


@dataclass(frozen=True)
class WorkTableRecovery:
    """
    Registered recovery and fault accounting for work tables behind one ProviderControl.

    Every in-flight binding over work tables needs the same two callbacks: run each
    capability's registered recovery through the broker, and accept exactly the worker
    errors that injected provider faults explain. Anything else stays a failure.
    """

    background: BackgroundWork
    provider: ProviderControl
    models: tuple[type[UniverseDurableWork], ...]
    """Work-table models whose registered recoveries run, in order."""
    injected: tuple[type[BaseException], ...]
    """Error types an injected fault surfaces as (a timeout, an unknown-outcome error)."""

    def recover(self) -> None:
        for model in self.models:
            with self.provider.explained_failures(*self.injected):
                self.background.run_recovery(registration_for(model).name)

    def account(self) -> None:
        assert len(self.background.failures) <= len(self.provider.reached), "more worker errors than injected faults"
        failures = self.background.unexpected_failures()
        if failures:
            errors: list[type[BaseException]] = []
            for failure in failures:
                assert failure.error is not None
                assert isinstance(failure.error, self.injected), repr(failure.error)
                errors.append(type(failure.error))
            self.background.expect_failures(*errors)
