"""Kill a real checkout worker at committed business boundaries."""

import sys
from unittest.mock import patch
from uuid import UUID

import django
from django.test import override_settings
from pytest_obligation.process_histories import die_here

django.setup()

from due.django.testing import hold_nudges  # noqa: E402
from examples.widget_store import services  # noqa: E402
from examples.widget_store.effects import checkout_order  # noqa: E402
from examples.widget_store.models import CheckoutWork  # noqa: E402


def main() -> None:
    decision, identity = sys.argv[1].split(":")
    advance = services.advance

    def transition(key: UUID, stage: int) -> float | None:
        die_here("before transition")
        result = advance(key, stage)
        if stage == 11:
            die_here("after decision")
        if stage == 6:
            die_here("mid dispatch")
        if stage == 1:
            die_here("after dispatch")
        return result

    with hold_nudges(), override_settings(WIDGET_STAGE_SECONDS=0), patch.object(services, "advance", transition):
        services.restock()
        order = services.checkout(UUID(identity))
        if decision == "failed":
            die_here("after reservation")
        services.payment(order.submission_key, decision)
        die_here("after payment receipt")
        assert checkout_order.attempt_execution(CheckoutWork.objects.get(owner=order).pk)


if __name__ == "__main__":
    main()
