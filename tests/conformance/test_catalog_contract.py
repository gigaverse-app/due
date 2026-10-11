from due.django.conformance.suite import effect_contract_suite
from examples.catalog.effects import project_catalog
from tests.conformance.catalog_examples import EXAMPLES


@effect_contract_suite(project_catalog, examples=EXAMPLES)
class TestCatalogProjection:
    pass
