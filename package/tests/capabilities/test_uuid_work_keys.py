"""UUID work keys cross real admission, evidence and JSON transport boundaries."""

from unittest.mock import patch
from uuid import UUID

import pytest
from kombu.serialization import dumps, loads

from due.django.evidence.attempts import attempts_for
from due.django.models import DurableWorkState
from due.django.runtime.tasks import run_durable_work_task
from due.django.testing import hold_nudges
from due.errors import EffectOutcomeUnknownError
from tests.harness.broker import real_broker as real_broker
from tests.harness.operation import ProviderResult
from tests.harness.primitive import PrimitiveOwner
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.uuid_work import UUIDPublication, admit
from tests.harness.uuid_work import uuid_publication as uuid_publication

pytestmark = pytest.mark.django_db(transaction=True)


def test_uuid_admission_executes_once_and_correlates_receipt(uuid_publication: UUIDPublication) -> None:
    work = admit(uuid_publication)
    assert isinstance(work.pk, UUID)
    assert uuid_publication.attempt_execution(work.pk)
    assert not uuid_publication.attempt_execution(work.pk)
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "published"
    assert UUIDPublication.accepted == [work.pk]
    attempt = attempts_for(work).get()
    assert attempt.work_uuid == work.pk and attempt.work_id is None


def test_uuid_lost_reply_settles_from_late_evidence_without_resend(uuid_publication: UUIDPublication) -> None:
    work = admit(uuid_publication)
    UUIDPublication.lose_reply = True
    with pytest.raises(EffectOutcomeUnknownError):
        uuid_publication.attempt_execution(work.pk)
    attempt = attempts_for(work).get()
    uuid_publication.record_applied_effect_evidence(attempt, ProviderResult(value="published"))
    uuid_publication.derive()
    work.refresh_from_db()
    assert work.state == DurableWorkState.SUCCEEDED
    assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "published"
    assert UUIDPublication.accepted == [work.pk]


def test_uuid_lost_notification_recovers_through_json_task_transport(uuid_publication: UUIDPublication) -> None:
    with hold_nudges():
        work = admit(uuid_publication, notify=True)
    deliveries = []

    def publish(*, args, **options):
        content_type, encoding, payload = dumps(args, serializer="json")
        deliveries.append(loads(payload, content_type=content_type, content_encoding=encoding))

    with patch.object(run_durable_work_task, "apply_async", side_effect=publish):
        uuid_publication.recover()
    assert len(deliveries) == 1
    assert deliveries[0][1] == str(work.pk)
    assert run_durable_work_task(*deliveries[0])
    assert not run_durable_work_task(*deliveries[0])
    assert UUIDPublication.accepted == [work.pk]


def test_uuid_delivery_runs_in_real_prefork_worker(uuid_publication, real_broker):
    """The real Redis/JSON/worker boundary preserves the UUID and fences replay."""
    import time
    from dataclasses import replace

    from celery.contrib.testing.worker import start_worker
    from django.db import connections

    from due.django.runtime.registry import installed

    app = real_broker
    entry = replace(installed.get()["uuid_work_test"], queue=app.conf.task_default_queue)
    work = admit(uuid_publication)
    task = app.tasks[run_durable_work_task.name]
    with patch.dict(installed.get(), {entry.name: entry}):
        uuid_publication.recover()
        work.refresh_from_db()
        token = str(work.fencing_token)
        connections.close_all()
        with start_worker(app, pool="prefork", concurrency=1, perform_ping_check=False, shutdown_timeout=15):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                work.refresh_from_db()
                if work.state == DurableWorkState.SUCCEEDED:
                    break
                time.sleep(0.05)
            assert work.state == DurableWorkState.SUCCEEDED
            assert PrimitiveOwner.objects.get(pk=work.owner_id).value == "published"
            duplicate = task.apply_async(args=(entry.name, str(work.pk), token), queue=entry.queue)
            assert duplicate.get(timeout=10) is False
            assert attempts_for(work).count() == 1
