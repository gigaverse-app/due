from datetime import datetime
from typing import Any
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError, CommandParser

from examples.scheduled.models import Publication
from examples.scheduled.services import (
    cancel_publication,
    create_publication,
    reschedule_publication,
    retry_publication,
)


class Command(BaseCommand):
    help = "Schedule, reschedule, cancel, retry or inspect a publication. Times must include a timezone."

    def add_arguments(self, parser: CommandParser) -> None:
        commands = parser.add_subparsers(dest="action", required=True)
        create = commands.add_parser("create")
        create.add_argument("text")
        create.add_argument("--submission-key", type=UUID)
        create.add_argument("--at", required=True, type=datetime.fromisoformat)
        for name in ("reschedule", "cancel", "retry", "show"):
            command = commands.add_parser(name)
            command.add_argument("id", type=int)
            if name == "reschedule":
                command.add_argument("--at", required=True, type=datetime.fromisoformat)

    def handle(self, *args: str, **options: Any) -> None:
        action = options["action"]
        try:
            if action == "create":
                publication = create_publication(
                    options["text"], options["at"], submission_key=options["submission_key"]
                )
                self.stdout.write(
                    f"Publication {publication.pk}: {publication.status} at {publication.publish_at.isoformat()}; submission_key={publication.submission_key}"
                )
            elif action == "show":
                publication = Publication.objects.get(pk=options["id"])
                self.stdout.write(
                    f"Publication {publication.pk}: {publication.status}; {publication.publish_at.isoformat()}; key={publication.published_key}; submission_key={publication.submission_key}"
                )
            elif action == "reschedule":
                self.stdout.write(reschedule_publication(options["id"], options["at"]).value)
            elif action == "cancel":
                self.stdout.write(cancel_publication(options["id"]).value)
            else:
                self.stdout.write("retry scheduled" if retry_publication(options["id"]) else "nothing to retry")
        except (ValueError, Publication.DoesNotExist) as error:
            raise CommandError(str(error)) from error
