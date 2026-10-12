from due.django.conformance.suite import effect_contract_suite
from examples.scheduled.effects import publish_at_deadline
from integration_tests.conformance.scheduled_examples import EXAMPLES


@effect_contract_suite(publish_at_deadline, examples=EXAMPLES)
class TestScheduledPublication:
    pass
