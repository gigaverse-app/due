"""
Self-test a work table's rejection matrix through profile J's classified-failure proof.

The root primitive's provider rejects every call. The application declares the
disposition and product outcome it intends; production's ``classify_error`` and
finalizers decide what actually happens. A mismatch in either direction, or a
classification the product never shows, must fail.
"""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import pytest
from pytest_obligation.contract import ObligationContractDesignError
from pytest_obligation.profiles.job_retry_limits import assert_classified_failure_contract

from due.django.conformance.bindings import DurableWorkBindings
from due.django.conformance.registrations import durable_work_installed
from due.django.conformance.suite import DeliveryExample, RejectionExample, classified_failure_bindings
from due.django.public import WorkFailureDisposition
from due.django.runtime.registry import DurableWorkRegistration
from tests.harness.primitive import PrimitiveEffect, PrimitiveOwner, PrimitiveWork, primitive, record
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

RETRYABLE, TERMINAL = WorkFailureDisposition.RETRYABLE, WorkFailureDisposition.TERMINAL


class ProviderRefused(Exception):
    """The injected provider rejection."""


def rejected(
    disposition: WorkFailureDisposition, *, expected: str = "before", provider_wait: timedelta | None = None
) -> Callable[[], AbstractContextManager[RejectionExample[PrimitiveWork, str]]]:
    @contextmanager
    def binding() -> Iterator[RejectionExample[PrimitiveWork, str]]:
        calls = 0

        def refuse(work: PrimitiveWork) -> str:
            nonlocal calls
            calls += 1
            raise ProviderRefused("injected provider rejection")

        with patch("tests.harness.primitive.call", refuse):
            work = record()
            yield RejectionExample(
                delivery=DeliveryExample(
                    work,
                    observe=lambda: PrimitiveOwner.objects.get(pk=work.owner_id).value,
                    expected=expected,
                    effect_calls=lambda: calls,
                ),
                disposition=disposition,
                provider_wait=provider_wait,
            )

    return binding


def production_classifies_as(disposition: WorkFailureDisposition) -> AbstractContextManager[object]:
    return patch.object(PrimitiveEffect, "classify_error", lambda self, error: disposition)


def assert_classified(factory: Callable[[], AbstractContextManager[RejectionExample[PrimitiveWork, str]]]) -> None:
    with classified_failure_bindings(DurableWorkBindings(primitive, record), factory, outcome="refused") as failure:
        assert_classified_failure_contract(failure)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("production", [RETRYABLE, TERMINAL])
@pytest.mark.parametrize("declared", [RETRYABLE, TERMINAL])
def test_a_rejection_must_take_the_declared_classification(
    declared: WorkFailureDisposition, production: WorkFailureDisposition
) -> None:
    mismatch = {
        (TERMINAL, RETRYABLE): "declared terminal was not terminal after one execution",
        (RETRYABLE, TERMINAL): "declared retryable became terminal after one execution",
    }.get((declared, production))
    with (
        production_classifies_as(production),
        pytest.raises(AssertionError, match=mismatch) if mismatch else nullcontext(),
    ):
        assert_classified(rejected(declared))


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("visible", [True, False], ids=["recorded", "invisible"])
def test_a_terminal_rejection_must_reach_the_product(visible: bool) -> None:
    def record_rejection(self: PrimitiveEffect, work: PrimitiveWork, error: Exception) -> None:
        PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="rejected")

    with (
        production_classifies_as(TERMINAL),
        patch.object(PrimitiveEffect, "finalize_terminal", record_rejection) if visible else nullcontext(),
        nullcontext() if visible else pytest.raises(AssertionError, match="invisible to the product"),
    ):
        assert_classified(rejected(TERMINAL, expected="rejected"))


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("visible", [True, False], ids=["recorded", "invisible"])
def test_a_retryable_rejection_waits_and_must_reach_the_product(visible: bool) -> None:
    # A positive retry delay makes an immediately due retry a hot loop (waits=True).
    delayed = replace(primitive, policy=replace(primitive.policy, retry_delay=timedelta(minutes=1)))
    entry = DurableWorkRegistration(name="primitive_delayed", capability=delayed, cadence_seconds=60, queue="celery")

    def record_retry(self: PrimitiveEffect, work: PrimitiveWork, error: Exception) -> None:
        PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="retrying")

    with (
        durable_work_installed(entry),
        production_classifies_as(RETRYABLE),
        patch.object(PrimitiveEffect, "finalize_retry", record_retry) if visible else nullcontext(),
        nullcontext() if visible else pytest.raises(AssertionError, match="invisible to the product"),
        classified_failure_bindings(
            DurableWorkBindings(delayed, record), rejected(RETRYABLE, expected="retrying"), outcome="throttled"
        ) as failure,
    ):
        assert failure.waits
        assert_classified_failure_contract(failure)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
def test_an_unknown_rejection_outside_an_observed_operation_is_refused() -> None:
    with pytest.raises(ObligationContractDesignError, match="UNKNOWN outcome needs provider observation"):
        assert_classified(rejected(WorkFailureDisposition.UNKNOWN))


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("honors_wait", [True, False], ids=["honored", "ignored"])
def test_declared_provider_wait_reaches_the_shared_proof(honors_wait: bool) -> None:
    wait = timedelta(minutes=10)
    delayed = replace(primitive, policy=replace(primitive.policy, retry_delay=timedelta(seconds=2)))
    entry = DurableWorkRegistration(name="primitive_throttled", capability=delayed, cadence_seconds=60, queue="celery")
    with (
        durable_work_installed(entry),
        production_classifies_as(RETRYABLE),
        patch.object(PrimitiveEffect, "provider_retry_after", lambda self, error: wait if honors_wait else timedelta()),
        classified_failure_bindings(
            DurableWorkBindings(delayed, record), rejected(RETRYABLE, provider_wait=wait), outcome="throttled"
        ) as failure,
    ):
        assert failure.provider_wait == wait
        with nullcontext() if honors_wait else pytest.raises(AssertionError, match="inside the provider's window"):
            assert_classified_failure_contract(failure)
