"""Admission preloads relationships after the owner lock without locking those rows."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import ClassVar

import pytest
from django.db import close_old_connections, connection, models, transaction
from django.db.models import Prefetch
from django.test.utils import CaptureQueriesContext, isolate_apps

from due.django.public import ReplaySafeEffect, UniverseDurableWork, durable_work_schema

with isolate_apps("tests"):

    class RelatedOwner(models.Model):
        value = models.CharField(max_length=40)
        parent = models.ForeignKey("self", null=True, on_delete=models.SET_NULL)

        class Meta:
            app_label = "tests"

    class RelatedWork(UniverseDurableWork):
        work_owner_field: ClassVar[str] = "owner"
        owner = models.OneToOneField(RelatedOwner, null=True, on_delete=models.SET_NULL)
        payload = models.CharField(max_length=40)

        class Meta:
            app_label = "tests"
            indexes, constraints = durable_work_schema("related_load")


class RelatedEffect(ReplaySafeEffect[RelatedOwner, RelatedWork, str]):
    def perform_effect(self, work: RelatedWork) -> str:
        return work.payload


@pytest.fixture
def related_rows() -> Iterator[RelatedOwner]:
    with connection.schema_editor() as editor:
        editor.create_model(RelatedOwner)
        editor.create_model(RelatedWork)
    try:
        ancestor = RelatedOwner.objects.create(value="ancestor")
        parent = RelatedOwner.objects.create(value="parent", parent=ancestor)
        yield RelatedOwner.objects.create(value="owner", parent=parent)
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(RelatedWork)
            editor.delete_model(RelatedOwner)


pytestmark = pytest.mark.django_db(transaction=True)


def related_capability() -> RelatedEffect:
    return RelatedEffect.for_owner(
        work=RelatedWork,
        owner_prefetch=(Prefetch("parent", queryset=RelatedOwner.objects.select_related("parent")),),
    )


def test_admission_loads_a_declared_relationship_chain_in_one_query(related_rows: RelatedOwner) -> None:
    capability = related_capability()
    with CaptureQueriesContext(connection) as queries:
        with capability.atomic(related_rows.pk, notify_worker=False) as pending:
            with CaptureQueriesContext(connection) as access:
                parent = pending.locked_owner.parent
                assert parent is not None and parent.parent is not None
                assert parent.parent.value == "ancestor"
            assert len(access) == 0
            assert pending.existing_work is None
    selects = [q["sql"] for q in queries if q["sql"].startswith("SELECT")]
    assert len(selects) == 3  # owner lock, work lookup, one joined relationship load
    assert "FOR UPDATE" in selects[0]
    assert "FOR UPDATE" not in selects[-1]


def test_admission_preserves_nullable_relationships(related_rows: RelatedOwner) -> None:
    capability = related_capability()
    related_rows.parent = None
    related_rows.save(update_fields=["parent"])
    with capability.atomic(related_rows.pk, notify_worker=False) as pending:
        assert pending.locked_owner.parent is None
        work = pending.request_work(RelatedWork(universe="acme", payload="no-parent"))
    assert work.payload == "no-parent"


def test_loading_relationships_does_not_extend_owner_lock_scope(related_rows: RelatedOwner) -> None:
    capability = related_capability()

    def lock_related() -> str:
        close_old_connections()
        try:
            with transaction.atomic():
                row = RelatedOwner.objects.select_for_update(nowait=True).get(pk=related_rows.parent_id)
                return row.value
        finally:
            close_old_connections()

    with capability.atomic(related_rows.pk, notify_worker=False) as pending:
        assert pending.locked_owner.parent is not None
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(lock_related).result(timeout=5) == "parent"


def test_related_snapshot_is_loaded_after_waiting_for_the_owner(related_rows: RelatedOwner) -> None:
    from threading import Event

    capability = related_capability()
    held, waiting = Event(), Event()

    def update_while_locked() -> None:
        close_old_connections()
        try:
            with transaction.atomic():
                RelatedOwner.objects.select_for_update().get(pk=related_rows.pk)
                held.set()
                assert waiting.wait(5), "admission never reached the owner lock"
                RelatedOwner.objects.filter(pk=related_rows.parent_id).update(value="committed-during-wait")
        finally:
            close_old_connections()

    def observe_lock_attempt(execute, sql, params, many, context):
        if "FOR UPDATE" in sql and RelatedOwner._meta.db_table in sql:
            waiting.set()
        return execute(sql, params, many, context)

    with ThreadPoolExecutor(max_workers=1) as pool:
        writer = pool.submit(update_while_locked)
        try:
            assert held.wait(5), "writer never held the owner"
            with connection.execute_wrapper(observe_lock_attempt):
                with capability.atomic(related_rows.pk, notify_worker=False) as pending:
                    assert pending.locked_owner.parent is not None
                    assert pending.locked_owner.parent.value == "committed-during-wait"
        finally:
            waiting.set()
            writer.result(timeout=5)
