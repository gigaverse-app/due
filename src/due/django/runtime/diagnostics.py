"""Read the actual process configuration and exported worker recovery evidence."""

from collections.abc import Iterable
from datetime import datetime

from celery import Celery
from django.conf import settings
from prometheus_client.parser import text_string_to_metric_families

from due.django.runtime.names import METRIC_NAMESPACE
from due.django.runtime.registry import RECOVERY_TASK, DurableWorkRegistration


def schedule_errors(entry: DurableWorkRegistration, app: Celery) -> list[str]:
    errors: list[str] = []
    if "due.django.runtime.tasks" not in (*app.conf.imports, *app.conf.include):
        errors.append("Due task imports are missing from the Celery app")
    configured = [
        item
        for item in settings.CELERY_BEAT_SCHEDULE.values()
        if item.get("task") == RECOVERY_TASK and tuple(item.get("args", ())) == (entry.name,)
    ]
    live = [
        item
        for item in app.conf.beat_schedule.values()
        if item.get("task") == RECOVERY_TASK and tuple(item.get("args", ())) == (entry.name,)
    ]
    if len(configured) != 1:
        errors.append(f"{entry.name}: require exactly one capability-specific Beat entry")
    else:
        actual = configured[0]
        if actual.get("schedule") != entry.cadence_seconds:
            errors.append(f"{entry.name}: recovery cadence drifted")
        options = actual.get("options", {})
        if options.get("queue") != entry.queue:
            errors.append(f"{entry.name}: recovery queue drifted")
        if options.get("expire_seconds") != entry.cadence_seconds:
            errors.append(f"{entry.name}: recovery expiry drifted")
    if live != configured:
        errors.append(f"{entry.name}: the Celery app does not see the generated Beat entry")
    return errors


def recovery_errors(registrations: Iterable[DurableWorkRegistration], metrics: str, *, now: datetime) -> list[str]:
    # An operator supplies a worker scrape. Reading this command's local metric
    # registry would falsely certify a different process or report fresh workers stale.
    samples: dict[str, dict[str, float]] = {}
    for family in text_string_to_metric_families(metrics):
        for sample in family.samples:
            if sample.name in {
                f"{METRIC_NAMESPACE}_last_recovery_timestamp_seconds",
                f"{METRIC_NAMESPACE}_recovery_stale_after_seconds",
            }:
                effect = sample.labels.get("effect")
                if effect is not None:
                    values = samples.setdefault(sample.name, {})
                    values[effect] = max(values.get(effect, 0), sample.value)
    errors = []
    for entry in registrations:
        effect = entry.model._meta.label_lower
        last = samples.get(f"{METRIC_NAMESPACE}_last_recovery_timestamp_seconds", {}).get(effect)
        threshold = samples.get(f"{METRIC_NAMESPACE}_recovery_stale_after_seconds", {}).get(effect)
        if last is None or threshold is None or last <= 0 or threshold <= 0:
            errors.append(f"{entry.name}: missing recovery heartbeat or stale threshold in worker metrics")
        elif not 0 <= now.timestamp() - last <= threshold:
            errors.append(f"{entry.name}: recovery heartbeat is stale or in the future")
    return errors
