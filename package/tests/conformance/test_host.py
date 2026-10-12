"""Application context and framework hooks stay visible to canonical fault proofs."""

from contextvars import ContextVar

import pytest
from django.dispatch import Signal
from pytest_obligation.host import hosted
from pytest_obligation.integrations.django.receivers import django_receiver_breaker
from pytest_obligation.profiles.automatic_recovery import assert_the_tick_restores_the_ambient_context
from pytest_obligation.worker_death import ReceiverFailed

from due.django.conformance.bindings import DurableWorkBindings
from due.django.conformance.host import work_table_host
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.primitive import primitive, record


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("leak", [False, True], ids=["restored", "leaked-application-context"])
def test_application_context_is_checked_by_the_shared_recovery_proof(leak: bool) -> None:
    tenant = ContextVar("application_tenant", default="request-tenant")
    host = work_table_host(production_packages={"tests.harness"}, ambient_context=tenant.get)
    assert assert_the_tick_restores_the_ambient_context in host.sweep_proofs
    with hosted(host), DurableWorkBindings(primitive, record).sweep() as sweep:
        original_tick = sweep.run_tick

        def tick() -> None:
            original_tick()
            if leak:
                tenant.set("left-behind-by-worker")

        sweep = sweep.model_copy(update={"run_tick": tick})
        if leak:
            with pytest.raises(AssertionError, match="left the ambient context"):
                assert_the_tick_restores_the_ambient_context(sweep)
        else:
            assert_the_tick_restores_the_ambient_context(sweep)


def test_application_receiver_faults_use_the_framework_breaker_and_restore_it() -> None:
    signal = Signal()
    received: list[str] = []

    def receiver(sender: object, **kwargs: object) -> None:
        received.append("handled")

    signal.connect(receiver, weak=False)
    host = work_table_host(
        production_packages={"tests.harness"},
        receiver_breaker=django_receiver_breaker(signal),
    )
    assert host.receiver_breaker is not None
    with host.receiver_breaker(None) as control:
        signal.send(sender=None)
    assert control.count == 1 and received == ["handled"]
    with host.receiver_breaker(1) as fault:
        with pytest.raises(ReceiverFailed):
            signal.send(sender=None)
    assert fault.count == 1 and received == ["handled"]
    signal.send(sender=None)
    assert received == ["handled", "handled"]
