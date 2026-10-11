"""
Declarations preserve fenced writes, locked snapshots and execution-local dependencies.

Design: docs/architecture.md.
"""

from contextvars import copy_context

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from due.django.declarations.capture import Capture
from due.django.declarations.external_services import ExternalService
from due.django.declarations.mirrors import StatusMirror
from due.django.runtime.coordination import finalize_under_fence
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


def owners(work: PrimitiveWork):
    return PrimitiveOwner.objects.filter(pk=work.owner_id)


def test_mirror_and_hook_commit_together_and_run_in_order(monkeypatch):
    mirror = StatusMirror(owners, touch="completed_at")
    outcome = mirror.outcome(lambda work, result: {"value": result})
    monkeypatch.setattr(PrimitiveEffect, "on_success", outcome)

    def hook(self, work, result):
        assert PrimitiveOwner.objects.get(pk=work.owner_id).value == result
        assert PrimitiveOwner.objects.get(pk=work.owner_id).completed_at is not None

    monkeypatch.setattr(PrimitiveEffect, "finalize_success", hook)
    work = record()
    assert primitive.attempt_execution(work.pk)
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == work.payload


def test_mirror_rolls_back_with_hook_failure(monkeypatch):
    monkeypatch.setattr(PrimitiveEffect, "on_success", StatusMirror(owners).outcome({"value": "mirrored"}))

    def fail(self, work, result):
        assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "mirrored"
        raise RuntimeError("hook failed")

    monkeypatch.setattr(PrimitiveEffect, "finalize_success", fail)
    work = record()
    with pytest.raises(RuntimeError, match="hook failed"):
        primitive.attempt_execution(work.pk)
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "before"


def test_mirror_requires_fence_and_preserves_excluded_owner():
    work = record()
    outcome = StatusMirror(owners).outcome({"value": "unknown"}, where=lambda rows: rows.exclude(value="posted"))
    with pytest.raises(RuntimeError, match="fenced settlement"), transaction.atomic():
        outcome(work, None)
    PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="posted")
    with transaction.atomic():
        finalize_under_fence(work, lambda current: outcome(current, None))
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "posted"


def test_mirror_required_match_rejects_superseded_target():
    work = record()
    outcome = StatusMirror(lambda value: owners(value).filter(value="gone")).outcome(
        {"value": "ready"}, no_match=lambda: RuntimeError("superseded")
    )
    with pytest.raises(RuntimeError, match="superseded"), transaction.atomic():
        finalize_under_fence(work, lambda current: outcome(current, None))


def test_exhaustion_uses_its_own_mirror_and_keeps_retry_hook(monkeypatch):
    monkeypatch.setattr(PrimitiveEffect, "on_retry", StatusMirror(owners).outcome({"value": "retry"}))
    monkeypatch.setattr(PrimitiveEffect, "on_exhausted", StatusMirror(owners).outcome({"value": "budget spent"}))
    seen = []

    def fail(self, work):
        raise OSError("outage")

    def hook(self, work, error):
        seen.append(PrimitiveOwner.objects.get(pk=work.owner_id).value)

    monkeypatch.setattr(PrimitiveEffect, "perform_effect", fail)
    monkeypatch.setattr(PrimitiveEffect, "finalize_retry", hook)
    work = record()
    for _attempt in range(4):
        with pytest.raises(OSError):
            primitive.attempt_execution(work.pk)
    assert seen == ["retry", "retry", "retry", "budget spent"]


def test_capture_uses_locked_owner_and_explicit_values_win():
    from due.django.capabilities.authoring import DurableWorkPolicy

    capability = PrimitiveEffect.for_owner(
        owner=PrimitiveOwner, work=PrimitiveWork, policy=DurableWorkPolicy(), capture=Capture(payload="value")
    )
    owner = PrimitiveOwner.objects.create(value="stale")
    PrimitiveOwner.objects.filter(pk=owner.pk).update(value="current")
    with capability.atomic(owner.pk, notify_worker=False) as pending:
        work = pending.request_work(universe="acme")
    assert work.payload == "current"
    with capability.atomic(owner.pk, notify_worker=False) as pending:
        work = pending.request_work(payload="explicit", universe="acme")
    assert work.payload == "explicit"


@pytest.mark.parametrize("preloaded", [False, True])
def test_capture_never_silently_loads_related_rows(preloaded: bool):
    rows = PrimitiveWork.objects.select_related("owner") if preloaded else PrimitiveWork.objects.all()
    work = rows.get(pk=record().pk)
    with CaptureQueriesContext(connection) as queries, pytest.raises(ValueError, match="prefetch"):
        Capture(payload="owner.value").read(work)
    assert len(queries) == 0
    with CaptureQueriesContext(connection) as queries:
        values = Capture(payload="owner.value", prefetch=True).read(work)
    assert values == {"payload": "before"}
    assert len(queries) == 1
    assert "FOR UPDATE" not in queries[0]["sql"]
    with CaptureQueriesContext(connection) as queries:
        assert Capture(payload="owner.value").read(work, payload=None) == {"payload": None}
    assert len(queries) == 0


def test_capture_rejects_coordination_fields():
    with pytest.raises(ValueError, match="domain field"):
        PrimitiveEffect.for_owner(
            owner=PrimitiveOwner, work=PrimitiveWork, policy=primitive.policy, capture=Capture(state="value")
        )


def test_provider_is_typed_cached_per_execution_and_invalid_after_return(monkeypatch):
    created = []
    copied = []
    external_service = ExternalService(lambda: created.append([]) or created[-1])

    def call(self, work):
        assert external_service() is external_service()
        copied.append(copy_context())
        return work.payload

    monkeypatch.setattr(PrimitiveEffect, "perform_effect", call)
    first, second = record(), record()
    assert primitive.attempt_execution(first.pk)
    assert primitive.attempt_execution(second.pk)
    assert len(created) == 2
    assert created[0] is not created[1]
    for context in copied:
        with pytest.raises(RuntimeError, match="execution"):
            context.run(external_service)
    assert not primitive.attempt_execution(first.pk)
    assert len(created) == 2


def test_provider_cannot_first_construct_inside_settlement(monkeypatch):
    external_service = ExternalService(lambda: "service")

    def hook(self, work, result):
        external_service()

    monkeypatch.setattr(PrimitiveEffect, "finalize_success", hook)
    with pytest.raises(RuntimeError, match="transaction"):
        primitive.attempt_execution(record().pk)


def test_provider_survives_commit_callbacks_but_not_failed_execution(monkeypatch):
    created = []
    external_service = ExternalService(lambda: created.append([]) or created[-1])
    observed = []

    def call(self, work):
        external_service().append(work.pk)
        return work.payload

    def hook(self, work, result):
        transaction.on_commit(lambda: observed.append(external_service()[0]))

    monkeypatch.setattr(PrimitiveEffect, "perform_effect", call)
    monkeypatch.setattr(PrimitiveEffect, "finalize_success", hook)
    work = record()
    primitive.attempt_execution(work.pk)
    assert observed == [work.pk]
    assert external_service() is not created[0]
    assert len(created) == 2


def test_nested_executions_restore_the_outer_provider(monkeypatch):
    created = []
    external_service = ExternalService(lambda: created.append([]) or created[-1])
    outer, inner = record(), record()

    def call(self, work):
        service = external_service()
        if work.pk == outer.pk:
            assert primitive.attempt_execution(inner.pk)
            assert external_service() is service
        return work.payload

    monkeypatch.setattr(PrimitiveEffect, "perform_effect", call)
    primitive.attempt_execution(outer.pk)
    assert len(created) == 2
    assert created[0] is not created[1]


def test_worker_death_revokes_copied_provider_scope(monkeypatch):
    from due.django.testing import WorkerDied

    copied = []
    external_service = ExternalService(lambda: "service")

    def call(self, work):
        assert external_service() == "service"
        copied.append(copy_context())
        raise WorkerDied()

    monkeypatch.setattr(PrimitiveEffect, "perform_effect", call)
    with pytest.raises(WorkerDied):
        primitive.attempt_execution(record().pk)
    with pytest.raises(RuntimeError, match="execution"):
        copied[0].run(external_service)


def test_two_executions_do_not_share_providers(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import connections

    barrier = Barrier(2, timeout=10)
    external_service = ExternalService(list)
    first, second = record(), record()
    observed = []

    def call(self, work):
        service = external_service()
        service.append(work.pk)
        barrier.wait()
        observed.append(list(external_service()))
        return work.payload

    def run(work):
        try:
            return primitive.attempt_execution(work.pk)
        finally:
            connections.close_all()

    monkeypatch.setattr(PrimitiveEffect, "perform_effect", call)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(copy_context().run, run, work) for work in (first, second)]
        assert all(result.result(timeout=20) for result in results)
    assert sorted(observed) == [[first.pk], [second.pk]]


def test_capture_overrides_cannot_write_kernel_or_owner_fields():
    from dataclasses import replace

    capability = replace(primitive, capture=Capture(payload="value"))
    owner = PrimitiveOwner.objects.create()
    for fields in ({"fencing_token": "invented"}, {"owner_id": owner.pk}, {"state": "succeeded"}):
        with capability.atomic(owner.pk, notify_worker=False) as pending:
            with pytest.raises(ValueError, match="domain fields"):
                pending.capture_work(**fields)


def test_closed_capture_transaction_cannot_be_reused():
    from dataclasses import replace

    capability = replace(primitive, capture=Capture(payload="value"))
    with capability.atomic(PrimitiveOwner.objects.create().pk, notify_worker=False) as pending:
        pass
    with pytest.raises(RuntimeError, match="inside atomic"):
        pending.capture_work()


def test_ordinary_callback_can_use_mirror_without_a_separate_outcome(monkeypatch):
    mirror = StatusMirror(owners, touch="completed_at")

    def publish(self, work, result):
        mirror.update(work, value=result)

    monkeypatch.setattr(PrimitiveEffect, "finalize_success", publish)
    work = record()
    with pytest.raises(RuntimeError, match="fenced settlement"):
        mirror.update(work, value="too early")
    assert primitive.attempt_execution(work.pk)
    owner = PrimitiveOwner.objects.get(pk=work.owner_id)
    assert owner.value == work.payload
    assert owner.completed_at is not None


def test_imperative_mirror_keeps_guard_and_required_match(monkeypatch):
    mirror = StatusMirror(owners)

    def publish(self, work, result):
        mirror.update(
            work,
            where=lambda rows: rows.exclude(value="before"),
            no_match=lambda: RuntimeError("product no longer accepts this result"),
            value=result,
        )

    monkeypatch.setattr(PrimitiveEffect, "finalize_success", publish)
    work = record()
    with pytest.raises(RuntimeError, match="no longer accepts"):
        primitive.attempt_execution(work.pk)
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "before"


def test_rich_outcome_mapping_can_use_field_names_reserved_by_update():
    """The optional keyword helper must not narrow the existing mapping API."""
    from unittest.mock import Mock

    from django.db.models import QuerySet

    rows = Mock(spec=QuerySet)
    outcome = StatusMirror(lambda work: rows).outcome({"where": "location", "no_match": "product value"})
    work = record()
    with transaction.atomic():
        finalize_under_fence(work, lambda current: outcome(current, None))
    rows.update.assert_called_once_with(where="location", no_match="product value")
