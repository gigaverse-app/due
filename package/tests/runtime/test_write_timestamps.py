"""Persisted transition times come back from the same database write."""

from contextlib import nullcontext
from datetime import datetime, timedelta

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from due.django.evidence.attempts import observe_locked_effect_outcome, record_effect_attempt
from due.django.models import EffectAttempt, EffectAttemptOutcome
from due.django.runtime.coordination import ReopenAfter, claim_work, complete_work, database_now
from due.django.testing import use_database_clock
from tests.harness.primitive import PrimitiveWork, record
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]


@pytest.mark.parametrize("outcome", [EffectAttemptOutcome.APPLIED, EffectAttemptOutcome.NOT_APPLIED])
@pytest.mark.parametrize("controlled_clock", [False, True])
def test_observation_returns_its_persisted_timestamp_without_a_clock_round_trip(outcome, controlled_clock):
    work = record()
    claim = claim_work(PrimitiveWork, work.pk, lease_duration=timedelta(minutes=5))
    assert claim is not None
    attempt = record_effect_attempt(claim)
    before = database_now()
    expected = before + timedelta(seconds=1)
    clock = use_database_clock(lambda: expected) if controlled_clock else nullcontext()
    with clock, transaction.atomic():
        current = EffectAttempt.objects.select_for_update().get(pk=attempt.pk)
        with CaptureQueriesContext(connection) as queries:
            observe_locked_effect_outcome(current, outcome)
        assert isinstance(current.observed_at, datetime)
        observed_at = current.observed_at
        if controlled_clock:
            assert observed_at == expected
        else:
            assert before <= observed_at <= database_now()
        assert len(queries) == 1, "observation should return its timestamp with the outcome write"
        assert queries[0]["sql"].startswith("UPDATE")
    current.refresh_from_db()
    assert current.outcome == outcome and current.observed_at == observed_at
    # Repeated evidence must not retime the original observation.
    with use_database_clock(lambda: observed_at + timedelta(days=1)), transaction.atomic():
        current = EffectAttempt.objects.select_for_update().get(pk=attempt.pk)
        with CaptureQueriesContext(connection) as queries:
            observe_locked_effect_outcome(current, outcome)
        assert len(queries) == 0
    current.refresh_from_db()
    assert current.observed_at == observed_at


@pytest.mark.parametrize("explicit_clock", [False, True])
def test_completion_hook_and_followup_receive_the_persisted_database_time(explicit_clock):
    work = record()
    claim = claim_work(PrimitiveWork, work.pk, lease_duration=timedelta(minutes=5))
    assert claim is not None
    database_time = database_now() + timedelta(seconds=1)
    completed_at = database_time + (timedelta(minutes=1) if explicit_clock else timedelta(0))
    seen = []

    def finalize(current):
        assert isinstance(current.completed_at, datetime)
        assert current.completed_at == completed_at
        assert PrimitiveWork.objects.get(pk=current.pk).completed_at == completed_at
        seen.append(current.completed_at)
        return ReopenAfter(delay=timedelta(minutes=2), reason="check again")

    clock = (lambda: completed_at) if explicit_clock else database_now
    with use_database_clock(lambda: database_time), CaptureQueriesContext(connection) as queries:
        assert complete_work(claim, finalize=finalize, now=clock)
    work.refresh_from_db()
    assert seen == [completed_at]
    assert work.available_at == completed_at + timedelta(minutes=2)
    assert not any(query["sql"] == "SELECT clock_timestamp()" for query in queries)


def test_default_completion_returns_a_real_database_timestamp_to_its_hook():
    work = record()
    claim = claim_work(PrimitiveWork, work.pk, lease_duration=timedelta(minutes=5))
    assert claim is not None
    before = database_now()
    seen = []

    def finalize(current):
        assert isinstance(current.completed_at, datetime)
        seen.append(current.completed_at)

    with CaptureQueriesContext(connection) as queries:
        assert complete_work(claim, finalize=finalize)
    after = database_now()
    work.refresh_from_db()
    assert seen == [work.completed_at]
    assert isinstance(work.completed_at, datetime)
    assert before <= work.completed_at <= after
    assert not any(query["sql"] == "SELECT clock_timestamp()" for query in queries)
