"""Requests compare explicit inputs and preserve execution progress."""

from collections.abc import Iterator
from typing import ClassVar

import pytest
from django.db import connection, models
from django.test.utils import isolate_apps

from due.django.capabilities.authoring import DurableWorkPolicy, ReplaySafeEffect
from due.django.declarations.capture import Capture
from due.django.models import UniverseDurableWork
from tests.harness.primitive import PrimitiveOwner
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

with isolate_apps("tests"):

    class InputWork(UniverseDurableWork):
        work_owner_field: ClassVar[str] = "owner"
        owner = models.OneToOneField(PrimitiveOwner, on_delete=models.SET_NULL, null=True)
        payload = models.CharField(max_length=40)
        auth_refresh_pending = models.BooleanField(default=False)

        class Meta:
            app_label = "tests"


class Effect(ReplaySafeEffect[PrimitiveOwner, InputWork]):
    def perform_effect(self, work: InputWork) -> None:
        pass


def bind(*, fields=("universe", "payload"), capture=None):
    return Effect.for_owner(
        owner=PrimitiveOwner, work=InputWork, policy=DurableWorkPolicy(), input_fields=fields, capture=capture
    )


@pytest.fixture
def input_tables(isolated_primitive: None) -> Iterator[None]:
    with connection.schema_editor() as editor:
        editor.create_model(InputWork)
    try:
        yield
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(InputWork)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("input_tables")
@pytest.mark.parametrize("changed", [False, True])
def test_request_preserves_progress_and_only_changes_revision_for_input(changed):
    capability = bind(capture=Capture(payload="value"))
    owner = PrimitiveOwner.objects.create(value="same")
    with capability.atomic(owner.pk, notify_worker=False) as pending:
        work = pending.request_work(universe="acme")
    revision = work.desired_revision
    InputWork.objects.filter(pk=work.pk).update(auth_refresh_pending=True)
    if changed:
        PrimitiveOwner.objects.filter(pk=owner.pk).update(value="new")
    with capability.atomic(owner.pk, notify_worker=False) as pending:
        requested = pending.request_work(universe="acme")
    assert requested.pk == work.pk
    assert requested.auth_refresh_pending
    assert requested.desired_revision == revision + int(changed)
    assert requested.payload == ("new" if changed else "same")


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("input_tables")
def test_request_cannot_write_progress():
    capability = bind()
    owner = PrimitiveOwner.objects.create()
    with capability.atomic(owner.pk, notify_worker=False) as pending:
        with pytest.raises(TypeError, match="progress field auth_refresh_pending"):
            pending.request_work(InputWork(universe="acme", payload="same", auth_refresh_pending=True))
    assert not InputWork.objects.exists()


@pytest.mark.no_db
@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(("payload",), id="missing-tenant"),
        pytest.param(("universe", "payload", "payload"), id="duplicate"),
        pytest.param(("universe", "state"), id="coordination-field"),
        pytest.param(("universe", "owner"), id="owner-field"),
        pytest.param(("universe", "typo"), id="unknown-field"),
    ],
)
def test_invalid_input_declaration_fails_at_binding(fields):
    with pytest.raises(ValueError, match="input_fields"):
        bind(fields=fields)


@pytest.mark.no_db
def test_capture_cannot_bypass_input_declaration():
    with pytest.raises(ValueError, match="domain field"):
        bind(capture=Capture(auth_refresh_pending="value"))


@pytest.mark.no_db
def test_rich_api_retains_inference_until_explicitly_migrated():
    fields = {field.name for field in bind(fields=None).domain_fields}
    assert fields == {"universe", "payload", "auth_refresh_pending"}
