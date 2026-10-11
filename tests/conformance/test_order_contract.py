from due.django.conformance.suite import effect_contract_suite
from examples.orders.effects import print_order
from tests.conformance.order_examples import EXAMPLES


@effect_contract_suite(print_order, examples=EXAMPLES)
class TestPrintOrder:
    pass
