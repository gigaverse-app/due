"""A pytest-obligation host wired to Due's Django and Celery boundaries."""

from collections.abc import Callable, Collection

from celery.exceptions import SoftTimeLimitExceeded
from pytest_obligation.host import Host, ReceiverBreaker
from pytest_obligation.integrations.celery import celery_publication_breaker, celery_publications
from pytest_obligation.integrations.django import django_host

from due.django.integration import current_tenant


def work_table_host(
    production_packages: Collection[str],
    *,
    replica_aliases: Collection[str] | None = None,
    ambient_context: Callable[[], object] = current_tenant,
    receiver_breaker: ReceiverBreaker | None = None,
) -> Host:
    """Configure real commit, connection, broker, clock and worker-stop faults.

    Pass application packages so the shared harness can verify that bindings
    reach production. Observe the application's own ambient context when it
    differs from Due's tenant context, and optionally bind framework hook faults.
    No application code or registrations are installed here.
    """
    return django_host(
        production_packages={"due", *production_packages},
        replica_aliases=replica_aliases,
        ambient_context=ambient_context,
        receiver_breaker=receiver_breaker,
        publication_recorder=celery_publications,
        publication_breaker=celery_publication_breaker,
        stop_exception=SoftTimeLimitExceeded,
    )
