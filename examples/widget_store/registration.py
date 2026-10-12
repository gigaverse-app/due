from due.django.runtime.registry import DurableWorkRegistration
from examples.widget_store.effects import checkout_order

REGISTRATION = DurableWorkRegistration(
    name="checkout_order",
    capability=checkout_order,
    cadence_seconds=1,
    execution_time_limit=18,
    execution_soft_time_limit=16,
)
