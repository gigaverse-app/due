"""The runnable application consumes Due's complete public suite generator."""

from dataclasses import replace

from due.django.conformance.regressions import EffectRegression
from due.django.conformance.suite import effect_contract_suite
from examples.documents.effects import publish_document
from integration_tests.conformance.document_examples import EXAMPLES


@effect_contract_suite(publish_document, examples=replace(EXAMPLES, regressions=(EffectRegression.LOST_PUBLICATION,)))
class TestDocumentPublication:
    pass
