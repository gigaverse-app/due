from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from examples.catalog.models import CatalogWork
from examples.catalog.services import refresh_entry


class Command(BaseCommand):
    help = "Restore a catalog entry in the external index; recovery finishes interrupted refreshes."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("entry_id", type=int)

    def handle(self, *args: str, **options: Any) -> None:
        try:
            work = refresh_entry(options["entry_id"])
        except CatalogWork.DoesNotExist as error:
            raise CommandError("No retained catalog projection exists for that entry") from error
        except OSError as error:
            raise CommandError("Index unavailable; refresh remains owed and recovery will retry") from error
        self.stdout.write(f"Catalog entry {work.owner_id}: refresh {work.state}; index key {work.index_key}")
