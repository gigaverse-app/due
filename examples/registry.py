"""One application manifest, installed identically in web, worker and Beat."""

from due.django.runtime.registry import DurableWorkRegistration
from examples.books.effects import index_book, publish_book
from examples.catalog.effects import project_catalog
from examples.documents.effects import publish_document
from examples.orders.effects import print_order
from examples.scheduled.effects import publish_at_deadline
from examples.widget_store.effects import checkout_order

REGISTRATIONS = (
    DurableWorkRegistration(
        name="checkout_order",
        capability=checkout_order,
        cadence_seconds=1,
        execution_time_limit=18,
        execution_soft_time_limit=16,
    ),
    DurableWorkRegistration(name="publish_at_deadline", capability=publish_at_deadline, cadence_seconds=5),
    DurableWorkRegistration(name="project_catalog", capability=project_catalog, cadence_seconds=5),
    DurableWorkRegistration(name="print_order", capability=print_order, cadence_seconds=5),
    DurableWorkRegistration(name="publish_document", capability=publish_document, cadence_seconds=5),
    DurableWorkRegistration(name="index_book", capability=index_book, cadence_seconds=5),
    DurableWorkRegistration(name="publish_book", capability=publish_book, cadence_seconds=5),
)
