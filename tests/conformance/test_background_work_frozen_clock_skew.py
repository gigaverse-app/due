"""Entering the frozen scheduler clock never moves either clock backwards."""

from datetime import timedelta

import pytest
import time_machine
from django.utils import timezone

from due.django.conformance.background import frozen_scheduler_clock
from due.django.runtime.coordination import database_now

# Locally, Docker's PostgreSQL VM clock drifts from the host by up to ~0.6 s in
# either direction; CI shares one kernel clock. Two seconds dominates both.
HOST_SKEW = timedelta(seconds=2)


@pytest.mark.django_db
@pytest.mark.parametrize("host_offset", [-HOST_SKEW, HOST_SKEW], ids=["host_behind_db", "host_ahead_of_db"])
def test_frozen_clock_starts_no_earlier_than_either_clock(host_offset: timedelta) -> None:
    """
    INVARIANT: a timestamp taken before entry, by the host or by PostgreSQL,
    is not in the future inside the frozen scope.

    Rows stamped "due now" by a PostgreSQL default (e.g. `Now()`) must stay due
    when a history then freezes time; otherwise its recovery tick sees nothing.
    """
    with time_machine.travel(timezone.now() + host_offset, tick=True):
        host_before, db_before = timezone.now(), database_now()
        with frozen_scheduler_clock():
            frozen = database_now()
            assert frozen == timezone.now(), "the library and product clocks must stay one clock"
            assert frozen >= db_before, "a row stamped by PostgreSQL before entry must not be in the future"
            assert frozen >= host_before, "a row stamped by the host before entry must not be in the future"
