"""Real application mutations remain visible through provider and adopter cleanup."""

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext, suppress
from dataclasses import replace

import pytest

from due.django.conformance.seams import ProviderSeam
from due.django.conformance.suite import assert_lost_notification_reaches_domain_outcome
from examples.books.effects import IndexBook, index_book
from examples.books.models import IndexBookWork
from tests.conformance.book_examples import INDEX_EXAMPLES, INDEX_SEAM
from tests.examples.books_support import IndexCalls, index_environment

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("broken", [False, True])
def test_book_worker_verdict_survives_provider_cleanup(monkeypatch: pytest.MonkeyPatch, broken: bool) -> None:
    @contextmanager
    def swallowing_provider() -> Iterator[IndexCalls]:
        with suppress(AssertionError), index_environment() as provider:
            yield provider

    # Reuse the application's reviewed binding and real SQLite provider. Only
    # cleanup is defective; observations and expected product outcomes stay real.
    # ARRANGE: the existing create_book producer admits the occurrence.
    # REAL PRODUCTION: the actual IndexBook worker and its publication handoff.
    # EXTERNAL SEAM: the same real SQLite provider in a scope with broken cleanup.
    # OBSERVE: reuse the application's independent search and successor observations.
    seam = ProviderSeam(
        environment=swallowing_provider,
        make_work=INDEX_SEAM.make_work,
        observe=INDEX_SEAM.observe,
        delivered=INDEX_SEAM.delivered,
        calls=INDEX_SEAM.calls,
    )
    examples = replace(INDEX_EXAMPLES, delivery=seam.delivery)
    if broken:
        # Mutation: settle a made-up key without writing its search entry.
        def skip_index(self: IndexBook, work: IndexBookWork) -> str:
            return "never-written"

        monkeypatch.setattr(IndexBook, "perform_effect", skip_index)
    with pytest.raises(AssertionError, match="worker reaches the required domain outcome") if broken else nullcontext():
        assert_lost_notification_reaches_domain_outcome(index_book, examples)


@pytest.mark.parametrize("proof", ["assert_first_execution_has_visible_effect", "assert_replay_converges"])
def test_derived_book_replay_rejects_consistently_wrong_content(monkeypatch: pytest.MonkeyPatch, proof: str) -> None:
    from pytest_obligation.contract import contract_cases

    from due.django.conformance.suite import effect_contract
    from examples.books import search

    write = search.put

    def corrupt(entry: search.IndexEntry) -> str:
        return write(entry.model_copy(update={"title": "wrong title"}))

    # The real producer, worker and SQLite provider run; only the payload sent
    # across the provider boundary is corrupted. Matching wrong replays must fail.
    monkeypatch.setattr(search, "put", corrupt)
    contract = effect_contract(index_book, examples=INDEX_EXAMPLES)
    case = next(case.values[0] for case in contract_cases(contract) if case.id == f"REPLAY_SAFE_EXECUTION-{proof}")
    with pytest.raises(AssertionError, match="declared product outcome"):
        case.run()
