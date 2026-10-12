from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from examples.orders.services import order_print


class Command(BaseCommand):
    help = "Submit one immutable print order; uncertain provider outcomes require observation."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("title")

    def handle(self, *args: str, **options: Any) -> None:
        work = order_print(options["title"])
        self.stdout.write(f"Print order {work.owner_id} accepted; provider reference {work.reference}")
