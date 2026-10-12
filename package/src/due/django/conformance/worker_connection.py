"""Bounded worker connections shared with the generic Django obligation harness.

These compatibility exports preserve Due's conformance imports. The canonical
implementation lives in pytest-obligation so ownership races and work-table
histories use the same SQL bounds, cleanup and stalled-session termination.
"""

from pytest_obligation.integrations.django.worker_connections import (
    database_worker_connection as database_worker_connection,
)
from pytest_obligation.integrations.django.worker_connections import (
    end_stalled_worker_sessions as end_stalled_worker_sessions,
)
from pytest_obligation.integrations.django.worker_connections import (
    worker_connection_wrapper as worker_connection_wrapper,
)
