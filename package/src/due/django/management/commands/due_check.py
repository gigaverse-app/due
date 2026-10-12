"""Configuration validation is local; recovery freshness needs worker evidence."""

from pathlib import Path
from typing import Any

from celery import current_app
from django.apps import apps
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from due.django.models import UniverseDurableWork
from due.django.runtime.diagnostics import recovery_errors, schedule_errors
from due.django.runtime.registry import index_durable_work_registrations, installed


class Command(BaseCommand):
    help = "Check local Due/Celery routing and optionally recovery freshness from exported worker metrics."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--metrics-file", type=Path, help="Prometheus text scraped from recovery workers")

    def handle(self, *args: str, **options: Any) -> None:
        registrations = index_durable_work_registrations(installed.get().values())
        if not registrations:
            raise CommandError("No Due capabilities registered")
        errors = [error for entry in registrations.values() for error in schedule_errors(entry, current_app)]
        registered_models = {entry.model for entry in registrations.values()}
        errors.extend(
            f"Unregistered work model: {model._meta.label_lower}"
            for model in apps.get_models()
            if issubclass(model, UniverseDurableWork) and model not in registered_models
        )
        if errors:
            raise CommandError("\n".join(errors))
        self.stdout.write(f"Configuration: OK ({len(registrations)} capabilities)")
        path = options["metrics_file"]
        if path is None:
            self.stdout.write("Recovery freshness: NOT CHECKED (supply --metrics-file from recovery workers)")
            return
        assert isinstance(path, Path)
        try:
            errors = recovery_errors(registrations.values(), path.read_text(), now=timezone.now())
        except (OSError, ValueError) as error:
            raise CommandError(f"Cannot read worker recovery metrics: {error}") from error
        if errors:
            raise CommandError("\n".join(errors))
        self.stdout.write("Recovery freshness: OK (supplied worker metrics)")
