"""Receipt/inventory histories from the observer binding every Due adopter already supplies."""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from django.db.models import Model
from pytest_obligation.interleavings import Bounds, EvidenceConfluence, EvidenceExpectation, EvidenceSession

from due.django.capabilities.observed_operations import ObservedExternalOperation, ObservedExternalOperationWork
from due.django.conformance.binding import Binding, entered_binding
from due.django.conformance.bindings import DurableWorkBindings
from due.django.conformance.interleavings import connection_scope, environment
from due.django.conformance.observations import ObservationExample
from due.django.testing import make_due, make_observation_due
from due.errors import EffectOutcomeUnknownError


def observation_confluence[OwnerT: Model, WorkT: ObservedExternalOperationWork, ResultT, ObservationT](
    name: str,
    capability: ObservedExternalOperation[OwnerT, WorkT, ResultT],
    example: Binding[ObservationExample[WorkT, ObservationT, ResultT]],
) -> EvidenceConfluence[bool]:
    """Reuse actual admission, provider reveal, product outcome and independent counters.

    The receipt and inventory each identify the complete immutable occurrence.
    Generated schedules deliver them in both orders, duplicate them, and insert
    recovery gaps. This does not model partial receipts or multiple resources:
    those need their own evidence declaration and reviewed outcomes.

    The example context must isolate its resources across exploration examples.
    ``reveal`` runs once per history, exposing provider state without writing
    application evidence; registered recovery must discover that same occurrence.
    """

    @contextmanager
    def bind() -> Iterator[EvidenceSession[bool]]:
        # ARRANGE: enter the adopter admission/provider scope and shared real-task recorder.
        with environment() as (background, _), entered_binding(example) as observed:
            delivery = observed.delivery
            calls = delivery.effect_calls
            assert calls is not None, "evidence confluence needs an independent create-call counter"
            creates = calls()
            work = delivery.work
            registration = DurableWorkBindings(capability, lambda: work).registration.name
            receipts: list[ResultT] = []

            def prepare() -> None:
                assert delivery.observe() != delivery.expected, "evidence must change the product outcome"
                make_due(work)
                make_observation_due(work)
                with pytest.raises(EffectOutcomeUnknownError):
                    background.run_recovery(registration)
                assert calls() == creates + 1, "positive control: real admission must reach the provider once"

            # EXTERNAL SEAM: expose only the provider occurrence, once, through the existing example.
            def reveal() -> ResultT:
                if not receipts:
                    receipts.append(observed.reveal())
                return receipts[0]

            # REAL PRODUCTION: public evidence ingestion/settlement and registered observer recovery.
            def receipt() -> None:
                # The public evidence route correlates the original immutable occurrence.
                assert capability.record_observed_effect_evidence(work.pk, reveal())
                capability.settle_from_applied_effect_evidence(work.pk)

            def inventory() -> None:
                reveal()
                make_observation_due(work)
                background.run_recovery(registration)

            def recover() -> None:
                background.run_recovery(registration)

            yield EvidenceSession(
                prepare=prepare,
                facts={"receipt": receipt, "inventory": inventory},
                # OBSERVE: the adopter product read and independent provider counter own the oracle.
                observe=lambda: delivery.observe() == delivery.expected,
                expectations={
                    facts: EvidenceExpectation(observation=bool(facts), effects={"create": 1})
                    for facts in (
                        frozenset(),
                        frozenset({"receipt"}),
                        frozenset({"inventory"}),
                        frozenset({"receipt", "inventory"}),
                    )
                },
                effects=lambda: {"create": calls() - creates},
                recover=recover,
                advance=background.advance,
                actor_scope=connection_scope,
            )

    return EvidenceConfluence(
        name=name,
        bind=bind,
        facts=("receipt", "inventory"),
        ordered_pair=("receipt", "inventory"),
        bounds=Bounds(interval_seconds=int(capability.observation_interval.total_seconds()) + 1),
        no_retry_because="An immutable uncertain occurrence cannot be resent; either complete evidence source settles it.",
    )
