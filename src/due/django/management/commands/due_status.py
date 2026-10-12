"""
Inspect stopped or uncertain work without granting permission to replay it.

Design and operator usage: docs/operations.md.
Application runbooks own evidence collection and authorized retry decisions.
"""

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.core.serializers.json import DjangoJSONEncoder

from due.django.capabilities.authoring import FAILED_TERMINAL_STATES
from due.django.evidence.attempts import unknown_effect_attempts
from due.django.models import DurableWorkState, RecoveryDeferral
from due.django.runtime.registry import installed


class Command(BaseCommand):
    help = (
        "List exhausted, terminal-failed and outcome-unknown work, and identities recovery is holding out with "
        "backoff. Read-only; use the application's recovery runbook."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("capability", nargs="?", help="Registered capability name; omit for every application")
        parser.add_argument("--limit", type=int, default=20, help="Oldest rows per capability (1–1000)")

    def handle(self, *args: str, **options: Any) -> None:
        registrations = installed.get()
        name = options["capability"]
        limit = options["limit"]
        assert name is None or isinstance(name, str)
        assert isinstance(limit, int)
        if not 1 <= limit <= 1000:
            raise CommandError("--limit must be between 1 and 1000")
        if name is not None and name not in registrations:
            raise CommandError(f"Unknown durable-work capability: {name}")
        for registration in sorted(registrations.values(), key=lambda entry: entry.name):
            if name is not None and name != registration.name:
                continue
            # A deliberate operator projection: no application payload, provider
            # receipt, user content or replay credential is exposed.
            rows = (
                registration.model.objects.filter(state__in=(*FAILED_TERMINAL_STATES, DurableWorkState.UNKNOWN))
                .order_by("created_at", "pk")
                .values(
                    "id",
                    "universe",
                    "state",
                    "desired_revision",
                    "rearm_count",
                    "last_error_code",
                    "created_at",
                    "updated_at",
                )[:limit]
            )
            self.stdout.write(
                json.dumps(
                    {
                        "capability": registration.name,
                        "work_model": registration.model._meta.label_lower,
                        "rows": list(rows),
                        "unknown_attempts": list(
                            unknown_effect_attempts(registration.model)
                            .order_by("started_at", "pk")
                            .values(
                                "id",
                                "work_id",
                                "desired_revision",
                                "started_at",
                                "outcome",
                                "repair_pending",
                                "repair_settled_by",
                            )[:limit]
                        ),
                        # Identities a recovery phase isolated and is holding out until
                        # retry_at; still owed. Exception strings can include payloads,
                        # so inspection intentionally omits last_error.
                        "recovery_deferrals": list(
                            RecoveryDeferral.objects.filter(effect=registration.model._meta.label_lower)
                            .order_by("first_failed_at", "pk")
                            .values(
                                "phase",
                                "identity",
                                "failures",
                                "first_failed_at",
                                "last_failed_at",
                                "retry_at",
                            )[:limit]
                        ),
                    },
                    cls=DjangoJSONEncoder,
                )
            )
