"""One application manifest, installed identically in web, worker and Beat."""

from due.django.runtime.registry import DurableWorkRegistration
from examples.books.effects import index_book, publish_book
from examples.catalog.effects import project_catalog
from examples.documents.effects import publish_document
from examples.orders.effects import print_order

REGISTRATIONS = (
    DurableWorkRegistration(name="project_catalog", capability=project_catalog, cadence_seconds=5),
    DurableWorkRegistration(name="print_order", capability=print_order, cadence_seconds=5),
    DurableWorkRegistration(name="publish_document", capability=publish_document, cadence_seconds=5),
    DurableWorkRegistration(name="index_book", capability=index_book, cadence_seconds=5),
    DurableWorkRegistration(name="publish_book", capability=publish_book, cadence_seconds=5),
)
