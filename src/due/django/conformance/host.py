"""A pytest-obligation host wired to Due's Django and Celery boundaries."""

from collections.abc import Collection

from celery.exceptions import SoftTimeLimitExceeded
from pytest_obligation.host import Host
from pytest_obligation.integrations.celery import celery_publication_breaker, celery_publications
from pytest_obligation.integrations.django import django_host

from due.django.integration import current_tenant


def work_table_host(production_packages: Collection[str], *, replica_aliases: Collection[str] | None = None) -> Host:
    """Configure real commit, connection, broker, clock and worker-stop faults.

    Pass application packages so the shared harness can verify that bindings
    reach production. No application code or registrations are installed here.
    """
    return django_host(
        production_packages={"due", *production_packages},
        replica_aliases=replica_aliases,
        ambient_context=current_tenant,
        publication_recorder=celery_publications,
        publication_breaker=celery_publication_breaker,
        stop_exception=SoftTimeLimitExceeded,
    )
