"""
Self-test the unbounded half of profile J against the real work-table runtime.

A capability with ``max_rearms=None`` binds the same failure example a bounded
one exhausts. Driven through production execution, a persistent provider outage
must leave the work owed: never terminal, paced by its retry delay, and due again
once that delay elapses. The negative control classifies the outage as permanent.
"""

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import timedelta
from functools import partial
from unittest.mock import patch

import pytest
from pytest_obligation.profiles.job_retry_limits import assert_persistent_retry_contract

from due.django.conformance.bindings import DurableWorkBindings
from due.django.conformance.registrations import durable_work_installed
from due.django.conformance.suite import failing_external_call, persistent_retry_bindings
from due.django.public import DurableWorkPolicy, WorkFailureDisposition
from due.django.runtime.registry import DurableWorkRegistration
from tests.harness.primitive import PrimitiveEffect, primitive, record
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)


@contextmanager
def unbounded(retry_delay: timedelta) -> Iterator[PrimitiveEffect]:
    """The root primitive under a policy that never exhausts, registered in its place."""
    capability = replace(primitive, policy=DurableWorkPolicy(max_rearms=None, retry_delay=retry_delay))
    entry = DurableWorkRegistration(
        name="primitive_unbounded", capability=capability, cadence_seconds=60, queue="celery"
    )
    with durable_work_installed(entry):
        yield capability


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("retry_delay", [timedelta(0), timedelta(minutes=1)], ids=["immediate", "delayed"])
@pytest.mark.parametrize("fault", [None, "classified_permanent"])
def test_a_persistent_outage_keeps_unbounded_work_owed(retry_delay: timedelta, fault: str | None) -> None:
    with (
        unbounded(retry_delay) as capability,
        patch.object(PrimitiveEffect, "classify_error", lambda self, error: WorkFailureDisposition.TERMINAL)
        if fault
        else nullcontext(),
        pytest.raises(AssertionError, match="became terminal after 1 execution") if fault else nullcontext(),
        persistent_retry_bindings(
            DurableWorkBindings(capability, record),
            partial(failing_external_call, "tests.harness.primitive.call"),
        ) as retry,
    ):
        # A zero delay makes the next due pass the retry; a positive one must not hot-loop.
        assert retry.waits is (retry_delay > timedelta(0))
        assert_persistent_retry_contract(retry)
