from due.django.conformance.suite import effect_contract_suite
from examples.widget_store.effects import checkout_order
from integration_tests.conformance.widget_examples import EXAMPLES


@effect_contract_suite(checkout_order, examples=EXAMPLES)
class TestWidgetCheckout:
    pass
