"""The real document producer and registered sweep expose a missing recovery implementation."""

import pytest

from due.django.conformance.regressions import EffectRegression, assert_effect_contract_detects_regression
from examples.documents.effects import publish_document
from integration_tests.conformance.document_examples import EXAMPLES

pytestmark = pytest.mark.django_db(transaction=True)


def test_document_contract_detects_disabled_lost_publication_recovery() -> None:
    assert_effect_contract_detects_regression(
        publish_document, examples=EXAMPLES, regression=EffectRegression.LOST_PUBLICATION
    )
