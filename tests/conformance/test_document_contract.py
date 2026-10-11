"""The runnable application consumes Due's complete public suite generator."""

from due.django.conformance.suite import effect_contract_suite
from examples.documents.effects import publish_document
from tests.conformance.document_examples import EXAMPLES


@effect_contract_suite(publish_document, examples=EXAMPLES)
class TestDocumentPublication:
    pass
