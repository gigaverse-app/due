from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from examples.catalog.services import save_entry


class Command(BaseCommand):
    help = "Create or edit a mutable catalog entry and durably request its projection."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("title")
        parser.add_argument("author")
        parser.add_argument("--id", type=int, dest="entry_id", help="Existing catalog entry to edit")

    def handle(self, *args: str, **options: Any) -> None:
        work = save_entry(options["title"], options["author"], entry_id=options["entry_id"])
        self.stdout.write(
            f"Catalog entry {work.owner_id}: revision {work.desired_revision} owed; index key {work.index_key}"
        )
