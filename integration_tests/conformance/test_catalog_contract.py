import pytest

from due.django.conformance.scenarios import assert_lost_acknowledgement_replay_converges
from due.django.conformance.suite import effect_contract_suite
from due.django.public import DurableWorkState
from examples.catalog.effects import project_catalog
from integration_tests.conformance.catalog_examples import EXAMPLES, reopening_delivery


@effect_contract_suite(project_catalog, examples=EXAMPLES)
class TestCatalogProjection:
    pass


@pytest.mark.django_db(transaction=True)
def test_lost_acknowledgement_keeps_repairing_an_older_unknown_catalog_write() -> None:
    with reopening_delivery() as example:
        assert_lost_acknowledgement_replay_converges(project_catalog, example, reference=reopening_delivery)
        example.work.refresh_from_db()
        assert example.work.state == DurableWorkState.READY
        assert example.observe() == example.expected
