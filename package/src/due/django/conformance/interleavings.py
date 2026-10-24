"""Due's broker, clock and provider adapters. Portable scheduling lives elsewhere."""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial

import pytest
from asgiref.sync import AsyncSingleThreadContext
from pytest_obligation.interleavings import InFlightSession, Intent, ProviderControl

from due.django.conformance.background import (
    BackgroundWork,
    accounted_background_work,
    frozen_scheduler_clock,
)
from due.django.conformance.worker_connection import database_worker_connection
from due.django.evidence.attempts import unsettled_work
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

    def session[W: UniverseDurableWork, V, O](
        self,
        *,
        intents: Mapping[str, Intent[V, O]],
        admit: Callable[[V], W],
        observe: Callable[[W], O],
        change: Callable[[W, V], object] | None = None,
        retire: Callable[[W], object] | None = None,
        retired: O | None = None,
        recreate: Callable[[W, V], W] | None = None,
        authority: Callable[[W], object] | None = None,
        rejection_errors: tuple[type[Exception], ...] = (),
        uncertain_repair_owed: Callable[[tuple[str, str]], bool] | None = None,
        retained: Callable[[W], bool] | None = None,
    ) -> InFlightSession[W, V, O]:
        """Bind application commands to standard Due transport and evidence observations.

        Admission captures real publications; start releases them and recovery
        invokes the registered capabilities. Revision observations refresh the
        actual work row. Retention includes UNKNOWN evidence from every revision,
        even when a later attempt completed. Override only ``retained`` when an
        application hands recovery ownership to another row.

        Retirement, recreation and rejected-command authority are product rules:
        pass real commands and immutable observations when declaring those
        histories. This adapter never invents them or writes expected outcomes.
        """
        broker = BrokerControl(self.background)

        def captured_admission(value: V) -> W:
            with broker.admission():
                work = admit(value)
                if type(work) not in self.models:
                    raise ValueError("admitted work must belong to a declared recovery model")
                return work

        def revision(work: W, field: str) -> str | None:
            work.refresh_from_db(fields=[field])
            value = getattr(work, field)
            return None if value is None else str(value)

        def desired(work: W) -> str:
            value = revision(work, "desired_revision")
            assert value is not None
            return value

        def retains_evidence(work: W) -> bool:
            model: type[UniverseDurableWork] = type(work)
            return unsettled_work(model.objects.filter(pk=work.pk), strict=True).exists()

        return InFlightSession(
            intents=intents,
            admit=captured_admission,
            start=lambda work: broker.start(),
            observe=observe,
            recover=self.recover,
            advance=self.background.advance,
            retained=retained or retains_evidence,
            provider=self.provider,
            transport=broker,
            account_faults=self.account,
            desired_identity=desired,
            acknowledged_identity=lambda work: revision(work, "applied_revision"),
            change=change,
            retire=retire,
            retired=retired,
            recreate=recreate,
            authority=authority,
            rejection_errors=rejection_errors,
            uncertain_repair_owed=uncertain_repair_owed,
        )
