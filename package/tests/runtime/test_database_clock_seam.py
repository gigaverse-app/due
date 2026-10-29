"""One post-lock clock transport serves both clock reads, and the testing clock controls both."""

from datetime import UTC, datetime
from uuid import uuid7

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from due.django.models import EffectAttempt
from due.django.runtime import coordination
from due.django.runtime.coordination import database_now
from due.django.testing import use_database_clock

pytestmark = pytest.mark.django_db(transaction=True)


def _evidence(*, present: bool):
    attempt = EffectAttempt.objects.create(work_model="tests.work", work_id=41, claim_token=uuid7(), desired_revision=1)
    return EffectAttempt.objects.filter(pk=attempt.pk if present else uuid7())


@pytest.mark.parametrize("present", [True, False])
def test_live_clock_reads_cost_one_statement_each(present):
    evidence = _evidence(present=present)
    with CaptureQueriesContext(connection) as plain:
        assert isinstance(database_now(), datetime)
    assert [q["sql"] for q in plain] == ["SELECT clock_timestamp()"]
    with CaptureQueriesContext(connection) as combined:
        started_at, matched = coordination._read_database_clock(evidence)
    assert len(combined) == 1
    assert combined[0]["sql"].startswith("SELECT clock_timestamp(), EXISTS (")
    assert isinstance(started_at, datetime)
    assert matched is present


@pytest.mark.parametrize("present", [True, False])
def test_testing_clock_controls_the_plain_and_the_exists_read(present):
    evidence = _evidence(present=present)
    controlled = datetime(2031, 1, 2, 3, 4, 5, tzinfo=UTC)
    with use_database_clock(lambda: controlled), CaptureQueriesContext(connection) as queries:
        assert database_now() == controlled
        assert coordination._read_database_clock() == (controlled, None)
        assert coordination._read_database_clock(evidence) == (controlled, present)
    # The predicate is still answered by PostgreSQL; only the clock is replaced.
    assert not any("clock_timestamp" in q["sql"] for q in queries)
    assert sum("EXISTS" in q["sql"] or "LIMIT 1" in q["sql"] for q in queries) == 1
