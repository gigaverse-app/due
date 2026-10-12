"""Telemetry outages cannot strand delivery or undo a provider completion."""

from typing import Never
from unittest.mock import patch

import pytest

from due.django.public import DurableWorkState
from tests.harness.primitive import (
    PrimitiveEffect,
    primitive,
    record,
)
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]


@pytest.mark.parametrize(
    "metric",
    [
        "_due_operations_total",
        "_due_outstanding",
        "_due_backlog",
        "_due_oldest_age_seconds",
        "_due_last_recovery",
    ],
)
def test_broken_metrics_and_reporter_preserve_recovery_and_completion(metric: str) -> None:
    works = [record(payload=value) for value in ("first", "second")]

    def unavailable(**labels: str) -> Never:
        raise OSError("metrics backend unavailable")

    with (
        patch(f"due.django.runtime.metrics.{metric}.labels", unavailable),
        patch("due.observability.sentry_sdk.capture_exception", side_effect=OSError("reporter down")),
    ):
        assert primitive.recover() == 2
        assert {identity for identity, _ in PrimitiveEffect.deliveries} == {work.pk for work in works}
        for identity, token in PrimitiveEffect.deliveries:
            assert primitive.attempt_execution(identity, token)
        for work in works:
            work.refresh_from_db()
            assert work.state == DurableWorkState.SUCCEEDED
            assert work.owner is not None
            assert work.owner.value == work.payload
        assert PrimitiveEffect.calls == ["first", "second"]
        assert primitive.recover() == 0
