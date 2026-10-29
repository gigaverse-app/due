"""A committed receipt can cross phases without becoming ownership authority."""

from uuid import uuid7

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from due.django.models import DurableWorkState, EffectAttempt
from tests.harness.bulk import result
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.self_work import SendChunk
from tests.harness.self_work import composed_chunk_environment as composed_chunk_environment
from tests.harness.self_work import composed_inline_chunk_environment as composed_inline_chunk_environment

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("layout", ["composed_chunk", "composed_inline_chunk"])
def test_committed_complete_evidence_is_not_loaded_again_for_settlement(request, layout):
    operation = request.getfixturevalue(layout)
    work = operation.model.objects.create(universe="acme", bulk_keys=["a", "b", "c"])
    with CaptureQueriesContext(connection) as queries:
        assert operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert work.delivered == ["a", "b", "c"]
    assert SendChunk.accepted_by_work[work.pk] == ["a", "b", "c"]
    # Receipt ingestion locks the attempt once. Completion retains the work
    # fence but does not need a second read of fully definite canonical evidence.
    reads = [
        q["sql"]
        for q in queries
        if q["sql"].startswith("SELECT")
        and f'FROM "{EffectAttempt._meta.db_table}"' in q["sql"]
        and "FOR UPDATE" in q["sql"]
    ]
    assert len(reads) == 1


@pytest.mark.parametrize("layout", ["composed_chunk", "composed_inline_chunk"])
@pytest.mark.parametrize("changed", ["token", "revision", "state"])
def test_committed_evidence_never_authorizes_a_stale_claim(request, layout, changed, monkeypatch):
    operation = request.getfixturevalue(layout)
    work = operation.model.objects.create(universe="acme", bulk_keys=["a", "b", "c"])
    apply = SendChunk.apply
    updates = {
        "token": {"fencing_token": uuid7()},
        "revision": {"desired_revision": 2},
        "state": {"state": DurableWorkState.UNKNOWN, "lease_expires_at": None},
    }[changed]

    def lose_authority(self, current, context):
        outcome = apply(self, current, context)
        self.model.objects.filter(pk=current.pk).update(**updates)
        return outcome

    monkeypatch.setattr(SendChunk, "apply", lose_authority)
    assert not operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.delivered == []
    assert SendChunk.accepted_by_work[work.pk] == ["a", "b", "c"]
    assert EffectAttempt.objects.get().repair_pending


@pytest.mark.parametrize("layout", ["composed_chunk", "composed_inline_chunk"])
def test_mutating_provider_objects_after_commit_cannot_change_the_handoff(request, layout, monkeypatch):
    operation = request.getfixturevalue(layout)
    response = result("a", "b", "c")
    SendChunk.script.append(response)
    apply = SendChunk.apply

    def mutate_after_commit(self, current, context):
        outcome = apply(self, current, context)
        response.items[0].key = "uncommitted-provider-mutation"
        return outcome

    monkeypatch.setattr(SendChunk, "apply", mutate_after_commit)
    work = operation.model.objects.create(universe="acme", bulk_keys=["a", "b", "c"])
    assert operation.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.delivered == ["a", "b", "c"]
    assert SendChunk.accepted_by_work[work.pk] == ["a", "b", "c"]


@pytest.mark.parametrize("layout", ["composed_chunk", "composed_inline_chunk"])
def test_custom_preparation_keeps_its_existing_call(request, layout, monkeypatch):
    operation = request.getfixturevalue(layout)
    prepare = SendChunk._prepare_success
    observed = []

    def custom(self, current, attempt):
        observed.append(current.pk)
        return prepare(self, current, attempt)

    monkeypatch.setattr(SendChunk, "_prepare_success", custom)
    work = operation.model.objects.create(universe="acme", bulk_keys=["a", "b", "c"])
    assert operation.attempt_execution(work.pk)
    assert observed == [work.pk]
    work.refresh_from_db()
    assert work.delivered == ["a", "b", "c"]
