"""Canonical provider types retain their first receipt; identity policy is explicit."""

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from django.db import connection, models
from django.test.utils import isolate_apps
from pydantic import TypeAdapter
from pytest_django.fixtures import DjangoAssertNumQueries

from due.django.evidence.attempts import record_effect_attempt
from due.django.evidence.effect_evidence import (
    AppliedEffectEvidenceStore,
    BinaryAppliedEffectEvidenceStore,
    JsonAppliedEffectEvidenceStore,
    StoredAppliedEffectEvidence,
)
from due.django.runtime.coordination import claim_work
from tests.harness.operation import (
    Publication,
    Receipt,
    record,
)
from tests.harness.operation import operation_environment as operation_environment
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = pytest.mark.django_db(transaction=True)


@dataclass(frozen=True)
class Resource:
    resource_id: str
    state: str


with isolate_apps("tests"):

    class BinaryReceipt(StoredAppliedEffectEvidence):
        result = models.BinaryField()

        class Meta:
            app_label = "tests"


@pytest.fixture
def binary_receipts(publication: Publication) -> Iterator[None]:
    with connection.schema_editor() as editor:
        editor.create_model(BinaryReceipt)
    try:
        yield
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(BinaryReceipt)


@pytest.mark.usefixtures("binary_receipts")
@pytest.mark.parametrize("encoding", ["json", "binary"])
@pytest.mark.parametrize("same_resource", [False, True])
@pytest.mark.parametrize("identity_policy", [False, True])
def test_canonical_dataclass_receipt_retains_first_observation(
    publication: Publication,
    same_resource: bool,
    identity_policy: bool,
    encoding: str,
    django_assert_num_queries: DjangoAssertNumQueries,
) -> None:
    work = record(publication)
    claim = claim_work(type(work), work.pk, lease_duration=publication.policy.lease_duration)
    assert claim is not None
    attempt = record_effect_attempt(claim)

    def same_occurrence(first: Resource, later: Resource) -> bool:
        return first.resource_id == later.resource_id

    equivalence = same_occurrence if identity_policy else None
    store: AppliedEffectEvidenceStore[Resource]
    if encoding == "json":
        store = JsonAppliedEffectEvidenceStore(Receipt, Resource, equivalent=equivalence)
    else:
        adapter = TypeAdapter(Resource)
        store = BinaryAppliedEffectEvidenceStore(
            BinaryReceipt, serialize=adapter.dump_json, deserialize=adapter.validate_json, equivalent=equivalence
        )
    original = Resource(resource_id="first", state="CREATING")
    store.record(attempt, original)
    # Duplicate delivery should consume the row it already read, not fetch it again.
    with django_assert_num_queries(1):
        store.record(attempt, original)
    later = Resource(resource_id="first" if same_resource else "other", state="RUNNING")
    if identity_policy and same_resource:
        store.record(attempt, later)
    else:
        with pytest.raises(ValueError, match="contradict"):
            store.record(attempt, later)
    # Stores implementing only record/read retain the backward-compatible
    # default, including canonicalization that keeps the first observation.
    assert store.record_and_read(attempt, original) == original
    assert store.read(attempt) == original
    assert (Receipt if encoding == "json" else BinaryReceipt).objects.count() == 1
