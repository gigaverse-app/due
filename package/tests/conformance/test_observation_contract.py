"""Observer enrollment must prove application-bound observation, not just execution."""

from contextlib import nullcontext
from dataclasses import replace

import pytest
from pytest_obligation.contract import ObligationContractDesignError
from pytest_obligation.crash_histories import (
    ExternalCall,
    assert_crash_at_every_commit_converges,
)

from due.django.conformance.background import BackgroundWork
from due.django.conformance.background import background_work as background_work
from due.django.conformance.background import scheduler_clock as scheduler_clock
from due.django.conformance.delivery import WorkTableDelivery
from due.django.conformance.observations import (
    ObservationExample,
    ObservationHistory,
    assert_observation_recovers_without_resend,
    observation_handoff,
)
from due.django.conformance.suite import (
    DeliveryExample,
    EffectExamples,
    effect_contract,
)
from tests.harness.enrollment import no_fixture
from tests.harness.observation import (
    ObservationProvider,
    ObservedPublication,
    ObservedWork,
)
from tests.harness.observation import observer as observer
from tests.harness.operation import ProviderResult
from tests.harness.primitive import PrimitiveOwner

pytestmark = pytest.mark.django_db(transaction=True)


def test_observer_cannot_enroll_without_application_observation(observer: ObservedPublication) -> None:
    examples = EffectExamples[ObservedWork](
        make_work=no_fixture,
        delivery=no_fixture,
        retry=no_fixture,
        terminal=no_fixture,
        applied_receipt=no_fixture,
        receipt_repair=no_fixture,
        rejections={"rejected": no_fixture},
        uncertainties={"unknown": no_fixture},
        immutable_inputs="An immutable publication occurrence.",
    )
    with pytest.raises(ObligationContractDesignError, match="observation"):
        effect_contract(observer, examples=examples)

    # Positive control: the feature declaration is sufficient; examples never
    # execute during collection, and no root fixture can masquerade as proof.
    effect_contract(observer, examples=replace(examples, observation=no_fixture))


def observation_example(observer: ObservedPublication) -> ObservationExample:
    ObservationProvider.result = None
    with observer.atomic(PrimitiveOwner.objects.create().pk, notify_worker=False) as pending:
        work = pending.request_work(ObservedWork(payload="applied", universe="TapIn"))

    def reveal() -> ProviderResult:
        ObservationProvider.result = ProviderResult(value="applied")
        return ObservationProvider.result

    return ObservationExample(
        delivery=DeliveryExample(
            work=work,
            observe=lambda: PrimitiveOwner.objects.get(pk=work.owner_id).value,
            expected="applied",
            effect_calls=lambda: len(ObservationProvider.creates),
        ),
        reveal=reveal,
        observation_calls=lambda: len(ObservationProvider.reads),
    )


@pytest.mark.parametrize(
    "history,fault,message",
    [
        ("inconclusive", None, None),
        ("lost_delivery", None, None),
        ("competing_receipt", None, None),
        ("inconclusive", "multiple_provider_reads", None),
        ("inconclusive", "inert_observer", "observation must settle the original obligation"),
        ("lost_delivery", "missing_dispatch", "reserve exactly one observation"),
        ("lost_delivery", "stale_token", "lost delivery token must be fenced"),
        ("inconclusive", "blind_counter", "inconclusive evidence must come from the provider"),
    ],
)
def test_observation_proof_rejects_broken_runtime(
    observer: ObservedPublication,
    background_work: BackgroundWork,
    monkeypatch: pytest.MonkeyPatch,
    history: ObservationHistory,
    fault: str | None,
    message: str | None,
) -> None:
    example = observation_example(observer)
    if fault == "multiple_provider_reads":

        def multiple_reads(self: ObservedPublication, work: ObservedWork) -> ProviderResult | None:
            # Valid adapter variation: one observation can need several provider
            # resources/pages. The harness must enforce ownership, not API count.
            ObservationProvider.observe(work.pk)
            return ObservationProvider.observe(work.pk)

        monkeypatch.setattr(ObservedPublication, "observe_effect", multiple_reads)
    elif fault == "blind_counter":
        example = replace(example, observation_calls=lambda: 0)
    elif fault == "inert_observer":

        def ignore_result(self: ObservedPublication, work: ObservedWork) -> None:
            ObservationProvider.observe(work.pk)

        monkeypatch.setattr(ObservedPublication, "observe_effect", ignore_result)
    elif fault == "missing_dispatch":
        monkeypatch.setattr(ObservedPublication, "_publish_observation", lambda *args: None)
    elif fault == "stale_token":

        def accept_stale(self: ObservedPublication, work_id: int, delivery_token: str) -> bool:
            self._observe(work_id)
            return True

        monkeypatch.setattr(ObservedPublication, "attempt_observation", accept_stale)
    expected = pytest.raises(AssertionError, match=message) if message else nullcontext()
    with expected:
        assert_observation_recovers_without_resend(observer, example, background_work, history=history)


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        (None, None),
        ("missing_dispatch", "reserve exactly one observation"),
        ("missing_publication", "positive control failed"),
    ],
)
def test_observer_handoff_reuses_the_example_and_rejects_broken_runtime(observer, monkeypatch, fault, message):
    if fault == "missing_dispatch":
        monkeypatch.setattr(ObservedPublication, "_publish_observation", lambda *args: None)
    elif fault == "missing_publication":
        monkeypatch.setattr(ObservedPublication, "_finalize_applied", lambda *args: None)
    history = observation_handoff(
        "observed-publication",
        observer,
        lambda: observation_example(observer),
        external_calls=(ExternalCall(ObservationProvider, "observe"),),
    )
    history = history.model_copy(
        update={"expected": {"product": True, "create_requests": 1}, "recovery_ticks": (observer.recover,)}
    )
    expected = pytest.raises(AssertionError, match=message) if message else nullcontext()
    with expected:
        assert_crash_at_every_commit_converges(WorkTableDelivery(observer), history)
