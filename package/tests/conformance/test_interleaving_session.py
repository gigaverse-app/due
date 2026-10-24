"""Work-table defaults observe the real runtime, including unresolved evidence."""

from datetime import timedelta
from functools import partial

import pytest
from pytest_obligation.interleavings import Fault, Intent, ProviderControl

from due.django.capabilities.authoring import DurableWorkPolicy
from due.django.capabilities.projection import Projection
from due.django.conformance.interleavings import WorkTableRecovery, environment
from due.django.evidence.attempts import attempts_for
from due.django.models import EffectAttemptOutcome
from due.django.runtime.registry import DurableWorkRegistration
from tests.harness import primitive as sut
from tests.harness.registrations import durable_work_installed

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("runtime")]


class TextProjection(Projection[sut.PrimitiveOwner, sut.PrimitiveWork]):
    def write_projection(self, work):
        sut.call(work)

    def on_projected(self, work):
        sut.PrimitiveOwner.objects.filter(pk=work.owner_id).update(value=work.payload)


projection = TextProjection.for_owner(
    owner=sut.PrimitiveOwner,
    work=sut.PrimitiveWork,
    input_fields=("universe", "payload"),
    policy=DurableWorkPolicy(retention=None, retry_delay=timedelta(0)),
)


def admit(value):
    owner = sut.PrimitiveOwner.objects.create()
    with projection.atomic(owner.pk) as pending:
        return pending.request_work(sut.PrimitiveWork(payload=value, universe="acme"))


@pytest.fixture
def runtime():
    entry = DurableWorkRegistration(
        name="primitive_self_test", capability=projection, cadence_seconds=60, queue="celery"
    )
    with sut.primitive_tables(), durable_work_installed(entry):
        yield


@pytest.mark.parametrize("lost_notification", [False, True])
def test_session_defaults_capture_deliver_recover_and_observe_revisions(lost_notification):
    with environment() as (background, _):
        provider = ProviderControl(accept=background.accept_provider_request)
        recovery = WorkTableRecovery(background, provider, (sut.PrimitiveWork,), (TimeoutError,))
        session = recovery.session(
            intents={"after": Intent(value="after", expected="after")},
            admit=admit,
            observe=lambda work: sut.PrimitiveOwner.objects.get(pk=work.owner_id).value,
        )
        assert session.transport is not None
        assert session.desired_identity is not None and session.acknowledged_identity is not None
        session.transport.drop(lost_notification)
        work = session.admit("after")
        assert session.observe(work) == "before", "admission must hold actual publications"
        assert session.retained(work)
        revision = session.desired_identity(work)
        assert session.acknowledged_identity(work) is None
        session.start(work)
        if lost_notification:
            assert session.observe(work) == "before"
            session.transport.drop(False)
            session.advance(600)
            session.recover()
        session.account_faults()
        assert session.observe(work) == "after"
        assert session.acknowledged_identity(work) == revision
        assert not session.retained(work)
        assert background.executed, "default wiring must run actual registered deliveries"


def test_session_retains_unknown_evidence_even_after_work_completes():
    with environment() as (background, patches):
        provider = ProviderControl(accept=background.accept_provider_request)
        original = sut.call
        patches.setattr(sut, "call", lambda work: provider.invoke("write", str(work.pk), lambda: original(work)))
        recovery = WorkTableRecovery(background, provider, (sut.PrimitiveWork,), (TimeoutError,))
        session = recovery.session(
            intents={"after": Intent(value="after", expected="after")},
            admit=admit,
            observe=lambda work: sut.PrimitiveOwner.objects.get(pk=work.owner_id).value,
        )
        provider.arm("write", Fault.HOLD)
        work = session.admit("after")
        session.start(work)
        session.account_faults()
        assert session.retained(work)
        session.advance(600)
        session.recover()
        session.account_faults()
        assert session.observe(work) == "after"
        assert attempts_for(work).filter(outcome=EffectAttemptOutcome.UNKNOWN).exists()
        assert session.retained(work), "a later success does not prove an older request finished"


def test_recovery_does_not_swallow_an_unexplained_error():
    with environment() as (background, patches):
        provider = ProviderControl(accept=background.accept_provider_request)
        recovery = WorkTableRecovery(background, provider, (sut.PrimitiveWork,), (TimeoutError,))

        def unrelated(name):
            raise TimeoutError("database read failed before any injected provider fault")

        patches.setattr(background, "run_recovery", unrelated)
        with pytest.raises(AssertionError, match="uninjected provider failure"):
            recovery.recover()


def test_retirement_session_observes_canonical_state_after_completed_work_is_purged():
    entry = DurableWorkRegistration(
        name="primitive_self_test", capability=sut.primitive, cadence_seconds=60, queue="celery"
    )
    with durable_work_installed(entry), environment() as (background, _):
        recovery = WorkTableRecovery(background, ProviderControl(), (sut.PrimitiveWork,), (TimeoutError,))

        def retire(work):
            sut.PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="retired")

        session = recovery.session(
            intents={"after": Intent(value="after", expected="after")},
            admit=partial(sut.record, notify_worker=True),
            observe=lambda work: sut.PrimitiveOwner.objects.get(pk=work.owner_id).value,
            retire=retire,
            retired="retired",
        )
        work = session.admit("after")
        session.start(work)
        assert session.observe(work) == "after"
        retire(work)
        session.advance(8 * 24 * 60 * 60)
        session.recover()
        session.account_faults()
        assert not sut.PrimitiveWork.objects.exists(), "the real recovery must cross the retention boundary"
        assert session.observe(work) == session.retired
        # The shared runner compares any supplied identities on every quiet-tail turn.
        # Retirement has no mutable revision, and canonical state survives valid row purging.
        if session.desired_identity is not None:
            assert session.acknowledged_identity is not None
            assert session.desired_identity(work) == session.acknowledged_identity(work)
