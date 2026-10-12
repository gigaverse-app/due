"""The real worker pool replaces killed sockets and runs the application contract."""

from contextlib import contextmanager
from copy import deepcopy
from uuid import uuid4

import psycopg
import pytest
from django.db import connections
from django.db.backends.postgresql.base import DatabaseWrapper
from pytest_obligation import contract_cases

from due.django.conformance.suite import effect_contract
from examples.widget_store.effects import checkout_order
from examples.widget_store.worker_settings import DATABASES
from integration_tests.conformance.widget_examples import EXAMPLES

pytestmark = pytest.mark.django_db(transaction=True)


@contextmanager
def worker_database(*, health_checks=None):
    previous = connections["default"]
    previous.close()
    config = deepcopy(previous.settings_dict)
    config["OPTIONS"]["pool"] = DATABASES["default"]["OPTIONS"]["pool"]
    config["CONN_HEALTH_CHECKS"] = (
        DATABASES["default"]["CONN_HEALTH_CHECKS"] if health_checks is None else health_checks
    )
    config["CONN_MAX_AGE"] = 0
    # A distinct pool alias prevents replacing any preexisting host pool.
    wrapper = DatabaseWrapper(config, alias=f"worker-proof-{uuid4().hex}")
    connections["default"] = wrapper
    try:
        yield wrapper
    finally:
        wrapper.close()
        wrapper.close_pool()
        connections["default"] = previous


def test_serial_worker_has_no_spare_stale_socket_after_session_loss():
    with worker_database() as database:
        database.ensure_connection()
        pool = database.pool
        assert pool is not None
        assert pool.max_size == 1, "serial worker must not grow spare idle sessions"
        assert database.connection is not None
        victim = database.connection.info.backend_pid
        database.close()
        with psycopg.connect(**database.get_connection_params(), autocommit=True) as observer:
            observer.execute("SELECT pg_terminate_backend(%s)", (victim,))
        before = pool.get_stats()
        with database.cursor() as cursor:
            cursor.execute("SELECT 17, pg_backend_pid()")
            answer, replacement = cursor.fetchone()
        assert answer == 17 and replacement != victim
        assert pool.get_stats().get("connections_lost", 0) - before.get("connections_lost", 0) == 1


@pytest.mark.parametrize("health_checks", [None, False], ids=["healthy", "negative-control-unchecked-socket"])
def test_generated_reconnected_dispatch_with_the_real_worker_pool(health_checks):
    case = next(
        row.values[0]
        for row in contract_cases(effect_contract(checkout_order, examples=EXAMPLES))
        if row.id == "delivery-replay-reconnected dispatch-assert_delivery_replay"
    )
    with worker_database(health_checks=health_checks):
        if health_checks is None:
            case.run()
        else:
            with pytest.raises(AssertionError, match="worker delivery failed after connection loss"):
                case.run()
