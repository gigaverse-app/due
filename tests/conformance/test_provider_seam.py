"""Bindings derived from a provider seam prove what hand-written bindings prove, and fail the same ways."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext, suppress
from dataclasses import dataclass, field, replace
from itertools import count
from typing import Any

import pytest
from pytest_obligation.contract import ObligationContractDesignError
from pytest_obligation.profiles.harmless_replay import (
    ReplaySafeEffect,
    assert_first_execution_has_visible_effect,
    assert_replay_converges,
)
from pytest_obligation.profiles.job_retry_limits import assert_bounded_retry_contract

from due.django.conformance.binding import entered_binding
from due.django.conformance.bindings import DurableWorkBindings
from due.django.conformance.seams import ProviderSeam, seam_examples
from due.django.conformance.suite import (
    assert_lost_notification_reaches_domain_outcome,
    retry_bindings,
)
from due.django.public import DurableWorkPolicy
from tests.harness.primitive import (
    PrimitiveEffect,
    PrimitiveOwner,
    PrimitiveWork,
    primitive,
    record,
)
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]


_payloads = count()


@dataclass
class Provider:
    """A provider that records each call and refuses every call once told to."""

    calls: list[str] = field(default_factory=list)
    stored: dict[int, str] = field(default_factory=dict)
    refusing: bool = False

    def call(self, work: PrimitiveWork) -> str:
        self.calls.append(work.payload)
        if self.refusing:
            raise ConnectionError("provider refused")
        self.stored[work.pk] = work.payload
        return work.payload


@contextmanager
def provider_environment() -> Iterator[Provider]:
    from unittest.mock import patch

    provider = Provider()
    with patch("tests.harness.primitive.call", provider.call):
        yield provider


def seam(**changes: Any) -> ProviderSeam[PrimitiveWork, Provider, str]:
    fields: dict[str, Any] = {
        "environment": provider_environment,
        "make_work": lambda: record(f"delivered-{next(_payloads)}"),
        "observe": lambda provider, work: PrimitiveOwner.objects.get(pk=work.owner_id).value,
        "delivered": lambda provider, work: work.payload,
        "calls": lambda provider: len(provider.calls),
        "calls_for": lambda provider, work: provider.calls.count(work.payload),
        "refuse": lambda provider: setattr(provider, "refusing", True),
        "provider_state": lambda provider, work: provider.stored.get(work.pk),
        "provider_expected": lambda provider, work: work.payload,
    }
    # ARRANGE: the primitive's own producer admits each work row.
    # REAL PRODUCTION: the primitive's perform_effect and its registered worker run unchanged.
    # EXTERNAL SEAM: only the module-level provider call is replaced.
    # OBSERVE: the owner row the worker settles, and the provider's call ledger.
    return ProviderSeam(**{**fields, **changes})


def assert_replay_behaviour(replay: ReplaySafeEffect) -> None:
    # The production-binding guard rejects the root primitive by design (its effect is
    # test code); a real adopter's derived execute is the capability's own method.
    assert_first_execution_has_visible_effect(replay)
    assert_replay_converges(replay)


def test_derived_bindings_prove_delivery_retry_and_replay() -> None:
    examples = seam_examples(primitive, seam(), immutable_inputs="An immutable test occurrence.")
    assert examples.retry is not None and examples.replay is not None and examples.convergence is None
    assert_lost_notification_reaches_domain_outcome(primitive, examples)
    with entered_binding(examples.replay) as replay:
        assert_replay_behaviour(replay)
    with retry_bindings(
        DurableWorkBindings(capability=primitive, make_work=examples.make_work), examples.retry
    ) as retry:
        assert_bounded_retry_contract(retry)


@contextmanager
def _swallowing_scope[T](factory: Callable[[], AbstractContextManager[T]]) -> Iterator[T]:
    with suppress(AssertionError), factory() as value:
        yield value


@pytest.mark.parametrize("scope", ["none", "provider", "application", "delivery"])
@pytest.mark.parametrize("broken", [False, True])
def test_delivery_verdict_survives_adopter_cleanup(scope: str, broken: bool) -> None:
    environment = (lambda: _swallowing_scope(provider_environment)) if scope == "provider" else provider_environment
    provider_seam = seam(
        environment=environment,
        **({"observe": lambda provider, work: "constant"} if broken else {}),
    )
    examples = seam_examples(primitive, provider_seam, immutable_inputs="An immutable test occurrence.")
    if scope == "application":
        examples = replace(examples, environment=lambda: _swallowing_scope(nullcontext))
    elif scope == "delivery":
        delivery = provider_seam.delivery
        examples = replace(examples, delivery=lambda: _swallowing_scope(delivery))
    verdict = (
        pytest.raises(AssertionError, match="worker reaches the required domain outcome") if broken else nullcontext()
    )
    with verdict:
        assert_lost_notification_reaches_domain_outcome(primitive, examples)


def test_a_refusal_that_never_fails_cannot_exhaust_retry() -> None:
    examples = seam_examples(primitive, seam(refuse=lambda provider: None))
    assert examples.retry is not None
    with pytest.raises(AssertionError):
        with retry_bindings(
            DurableWorkBindings(capability=primitive, make_work=examples.make_work), examples.retry
        ) as retry:
            assert_bounded_retry_contract(retry)


def test_a_call_count_that_never_counts_fails_replay() -> None:
    examples = seam_examples(primitive, seam(calls_for=lambda provider, work: 0))
    assert examples.replay is not None
    with pytest.raises(AssertionError), entered_binding(examples.replay) as replay:
        assert_replay_behaviour(replay)


def test_retry_is_derived_only_for_a_bounded_policy() -> None:
    unbounded = replace(primitive, policy=DurableWorkPolicy(max_rearms=None))
    assert seam_examples(unbounded, seam(refuse=None)).retry is None
    with pytest.raises(ObligationContractDesignError, match="must declare refuse"):
        seam_examples(primitive, seam(refuse=None))


def test_an_unannotated_seam_is_refused() -> None:
    with pytest.raises(ObligationContractDesignError, match="missing"):
        ProviderSeam(environment=provider_environment, make_work=record, observe=lambda p, w: None, delivered=lambda p, w: None, calls=lambda p: 0)  # fmt: skip


def test_outcome_factory_holds_its_notification_until_the_proof_delivers() -> None:
    binding = seam().outcome(
        lambda provider, work: None,
        expected=lambda provider, work: work.payload,
        make_work=lambda provider: record(notify_worker=True),
    )
    with binding() as example:
        assert PrimitiveEffect.deliveries == []
        assert example.observe() != example.expected
        assert primitive.attempt_execution(example.work.pk)
        assert example.observe() == example.expected


def test_explicit_retry_binding_replaces_derivation_without_requiring_refuse() -> None:
    explicit_retry = seam().retry
    examples = seam_examples(primitive, seam(refuse=None), retry=explicit_retry)
    assert examples.retry is explicit_retry
    with retry_bindings(
        DurableWorkBindings(capability=primitive, make_work=examples.make_work), examples.retry
    ) as retry:
        assert_bounded_retry_contract(retry)


def test_seam_examples_compile_through_the_public_contract_api() -> None:
    from due.django.conformance.suite import effect_contract

    provider = seam()
    examples = seam_examples(
        primitive,
        provider,
        terminal=provider.delivery,
        convergence_not_assessed="This control exercises replay enrollment; immutable admission has separate controls.",
    )
    contract = effect_contract(primitive, examples=examples)
    assert contract.replay is not None


@pytest.mark.parametrize("expected", [True, False])
def test_replay_can_declare_a_provider_outcome_different_from_delivery(expected: bool) -> None:
    examples = seam_examples(
        primitive,
        seam(
            provider_state=lambda provider, work: work.pk in provider.stored,
            provider_expected=lambda provider, work: expected,
        ),
    )
    assert examples.replay is not None
    with entered_binding(examples.replay) as replay:
        with nullcontext() if expected else pytest.raises(AssertionError, match="declared product outcome"):
            assert_replay_behaviour(replay)


def test_provider_replay_cannot_infer_its_requirement_from_observation() -> None:
    with pytest.raises(ObligationContractDesignError, match="provider_expected"):
        seam_examples(primitive, seam(provider_expected=None), immutable_inputs="An immutable test occurrence.")


def test_provider_replay_checks_the_independent_outcome() -> None:
    examples = seam_examples(
        primitive,
        seam(provider_expected=lambda provider, work: "wrong"),
        immutable_inputs="An immutable test occurrence.",
    )
    assert examples.replay is not None
    with entered_binding(examples.replay) as replay, pytest.raises(AssertionError, match="declared product outcome"):
        assert_first_execution_has_visible_effect(replay)
