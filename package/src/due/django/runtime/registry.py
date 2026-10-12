"""Declarative routing, cadence and task bounds for installed applications.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any, TypedDict

from celery import Celery
from django.conf import settings

from due._installed import Installed, NotInstalledError
from due.django.capabilities.authoring import DurableWorkCapability
from due.django.models import UniverseDurableWork
from due.django.runtime.limits import (
    EXECUTION_SOFT_TIME_LIMIT_SECONDS,
    EXECUTION_TIME_LIMIT_SECONDS,
)
from due.django.runtime.names import RECOVERY_TASK
from due.django.runtime.recovery_pass import DEFAULT_STALE_AFTER


@dataclass(frozen=True)
class DurableWorkRegistration[WorkT: UniverseDurableWork, ResultT]:
    """Auditable recovery ownership for one concrete work-table adopter."""

    name: str
    capability: DurableWorkCapability[WorkT, ResultT]
    cadence_seconds: int = 60
    # Default-routed work follows the environment, including isolated workers.
    # Explicit dedicated routes remain authoritative; production stays celery.
    queue: str = field(default_factory=lambda: getattr(settings, "CELERY_TASK_DEFAULT_QUEUE", "celery"))
    execution_time_limit: int = EXECUTION_TIME_LIMIT_SECONDS
    execution_soft_time_limit: int = EXECUTION_SOFT_TIME_LIMIT_SECONDS

    @property
    def model(self) -> type[WorkT]:
        """The routed object owns its model; registration cannot name another one."""
        return self.capability.model


class RecoveryTaskOptions(TypedDict):
    queue: str
    expire_seconds: int


class RecoverySchedule(TypedDict):
    """The Celery Beat configuration boundary generated from one registration."""

    task: str
    schedule: int
    args: tuple[str]
    options: RecoveryTaskOptions


def recovery_schedules(registrations: Iterable[DurableWorkRegistration]) -> dict[str, RecoverySchedule]:
    return {
        f"recover_{entry.name}": {
            "task": RECOVERY_TASK,
            "schedule": entry.cadence_seconds,
            "args": (entry.name,),
            # DatabaseScheduler persists expire_seconds and publishes expires.
            # Only the hint expires; PostgreSQL retains work for the next tick.
            "options": {"queue": entry.queue, "expire_seconds": entry.cadence_seconds},
        }
        for entry in registrations
    }


def remove_stale_recovery_schedules(
    registrations: Iterable[DurableWorkRegistration],
    *,
    retired_task_names: Iterable[str] = (),
) -> list[str]:
    """Remove only Due's unowned Beat entries; other applications' tasks are untouched.

    Requires the optional django-celery-beat integration. Call intentionally
    during application startup when using its database-backed scheduler.
    ``retired_task_names`` explicitly identifies older names owned by the same
    application. Owned schedule names remain for DatabaseScheduler to update.
    """
    from django_celery_beat.models import PeriodicTask

    stale = PeriodicTask.objects.filter(task__in=(RECOVERY_TASK, *retired_task_names)).exclude(
        name__in=recovery_schedules(registrations)
    )
    names = sorted(stale.values_list("name", flat=True))
    if names:
        stale.delete()
    return names


def index_durable_work_registrations(
    registrations: Iterable[DurableWorkRegistration],
) -> dict[str, DurableWorkRegistration]:
    """Build the runtime route table while rejecting ambiguous ownership."""

    by_name: dict[str, DurableWorkRegistration] = {}
    models: set[type[UniverseDurableWork]] = set()
    for registration in registrations:
        if registration.name in by_name:
            raise ValueError(f"duplicate durable-work registration name: {registration.name}")
        if registration.model in models:
            raise ValueError(f"duplicate durable-work work model: {registration.model._meta.label_lower}")
        if registration.cadence_seconds <= 0:
            raise ValueError(f"{registration.name}: cadence_seconds must be positive")
        if 2 * registration.cadence_seconds > DEFAULT_STALE_AFTER.total_seconds():
            # recover() publishes the default stale threshold: a slower cadence would page between runs.
            raise ValueError(
                f"{registration.name}: cadence_seconds={registration.cadence_seconds} is over half the stale "
                f"threshold recover() declares ({DEFAULT_STALE_AFTER}); a slower recovery needs recover() "
                f"to pass stale_after"
            )
        if not registration.queue:
            raise ValueError(f"{registration.name}: queue must be declared")
        if not 0 < registration.execution_soft_time_limit < registration.execution_time_limit:
            raise ValueError(f"{registration.name}: soft execution limit must precede the hard limit")
        by_name[registration.name] = registration
        models.add(registration.model)
    _check_declared_wakes(by_name.values())
    return by_name


def _check_declared_wakes(registrations: Iterable[DurableWorkRegistration]) -> None:
    """A process that would publish a wake nobody can route, or break a claim, refuses to start."""
    by_model = {registration.model: registration for registration in registrations}
    for registration in by_model.values():
        for dependent in registration.capability.wakes:
            label = dependent.work._meta.label_lower
            target = by_model.get(dependent.work)
            if target is None:
                raise ValueError(f"{registration.name}: wakes {label}, which no registration routes")
            if not target.capability.wakeable:
                raise ValueError(f"{registration.name}: wakes {target.name}, whose work its producer's claim owns")


class DurableWorkNotInstalledError(NotInstalledError):
    """A process published or ran registered work without installing the application's registrations."""


#: The registrations this process routes, keyed by route name. ``wire()`` installs the manifest's own dict,
#: by reference, so a test patching an entry changes what every later lookup sees.
installed: Installed[MutableMapping[str, DurableWorkRegistration]] = Installed(
    "durable-work registrations", DurableWorkNotInstalledError
)


def install_registrations(by_name: MutableMapping[str, DurableWorkRegistration]) -> None:
    """Make the application's registrations resolvable by route name and by work model. Idempotent."""
    installed.install(by_name)


def registration_named(name: str) -> DurableWorkRegistration:
    """The registration a queued message names; an unknown name raises ``KeyError``."""
    return installed.get()[name]


def registration_for(model: type[UniverseDurableWork]) -> DurableWorkRegistration:
    """
    The registration that routes a work model; an unregistered model raises ``KeyError``.

    The manifest routes each model once (``index_durable_work_registrations`` rejects a second); a test that
    patches in another registration for the same model gets the later one.
    """
    return {registration.model: registration for registration in installed.get().values()}[model]


def _merge_schedules(existing: Mapping[str, Any], generated: Mapping[str, RecoverySchedule]) -> dict[str, Any]:
    # Celery accepts application-defined schedule entries at this dynamic config
    # boundary. Only an entry for the same Due route belongs to this manifest.
    for name, entry in generated.items():
        previous = existing.get(name)
        if name in existing and (
            not isinstance(previous, Mapping)
            or previous.get("task") != RECOVERY_TASK
            or previous.get("args", ()) not in (entry["args"], list(entry["args"]))
        ):
            raise ValueError(f"recovery schedule {name!r} is owned by another task or route")
    return {**existing, **generated}


def configure_durable_work(
    registrations: Iterable[DurableWorkRegistration], *, celery_app: Celery | None = None
) -> None:
    """Install a manifest, task imports and schedules during application startup.

    Pass an explicit Celery app if its configuration is already loaded or it does
    not read Django's CELERY settings. Otherwise configure Celery from those
    settings normally. Call before serving work; this is not a hot-reload API.
    """
    by_name = index_durable_work_registrations(registrations)
    generated = recovery_schedules(by_name.values())
    schedules = _merge_schedules(getattr(settings, "CELERY_BEAT_SCHEDULE", {}), generated)
    imports = tuple(dict.fromkeys((*getattr(settings, "CELERY_IMPORTS", ()), "due.django.runtime.tasks")))
    # Validate both owners before mutating either or publishing the registry.
    # A collision must not leave half of an application wired to a new manifest.
    if celery_app is not None:
        app_schedules = _merge_schedules(celery_app.conf.beat_schedule, generated)
        app_imports = tuple(dict.fromkeys((*celery_app.conf.imports, *imports)))
        celery_app.conf.update(beat_schedule=app_schedules, imports=app_imports)
    settings.CELERY_BEAT_SCHEDULE = schedules
    settings.CELERY_IMPORTS = imports
    install_registrations(by_name)
