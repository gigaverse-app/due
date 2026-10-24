"""Concise authoring keeps the canonical owner, policies and settlement path."""

from datetime import timedelta
from typing import cast

import pytest

from due.django.public import DurableWorkPolicy, DurableWorkState
from tests.harness.operation import Publication, PublicationWork
from tests.harness.primitive import PrimitiveEffect, PrimitiveOwner, PrimitiveWork
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
def test_inferred_owner_and_default_policy_settle_the_product():
    capability = PrimitiveEffect.for_owner(work=PrimitiveWork)
    assert capability.owner_model is PrimitiveOwner
    assert capability.policy == DurableWorkPolicy()
    owner = PrimitiveOwner.objects.create()
    with capability.atomic(owner.pk, notify_worker=False) as pending:
        pending.locked_owner.value = "requested"
        pending.locked_owner.save(update_fields=["value"])
        work = pending.request_work(PrimitiveWork(payload="published", universe=""))
    assert capability.attempt_execution(work.pk)
    owner.refresh_from_db()
    work.refresh_from_db()
    assert owner.value == "published"
    assert work.state == DurableWorkState.SUCCEEDED
    assert PrimitiveEffect.calls == ["published"]


@pytest.mark.no_db
def test_explicit_owner_and_policy_keep_their_existing_meaning():
    policy = DurableWorkPolicy(retry_delay=timedelta(seconds=17), retention=None)
    capability = PrimitiveEffect.for_owner(work=PrimitiveWork, owner=PrimitiveOwner, policy=policy)
    assert capability.policy is policy
    with pytest.raises(TypeError, match="points to"):
        PrimitiveEffect.for_owner(work=PrimitiveWork, owner=cast(type[PrimitiveOwner], PrimitiveWork))


@pytest.mark.no_db
@pytest.mark.parametrize("field", [None, "payload"])
def test_inference_requires_the_declared_owner_relation(monkeypatch, field):
    monkeypatch.setattr(PrimitiveWork, "work_owner_field", field)
    with pytest.raises(TypeError, match="work_owner_field|OneToOneField"):
        PrimitiveEffect.for_owner(work=PrimitiveWork)


@pytest.mark.no_db
def test_evidence_required_operations_still_require_retained_history():
    with pytest.raises(ValueError, match="retain their occurrence"):
        Publication.for_owner(work=PublicationWork)
    capability = Publication.for_owner(work=PublicationWork, policy=DurableWorkPolicy(retention=None))
    assert capability.owner_model is PrimitiveOwner
