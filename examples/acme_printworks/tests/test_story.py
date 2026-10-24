from uuid import uuid4

import pytest
from django.db import transaction
from due.django.conformance.delivery import WorkTableDelivery
from due.django.runtime.registry import registration_for
from due.django.tasks.models import TaskWork
from due.django.testing import hold_nudges
from due.errors import EffectOutcomeUnknownError

from acme import providers
from acme.effects import cleanup_spool, order_printing, project_portal, publish_proof
from acme.models import CleanupWork, Job, PortalWork, Revision
from acme.services import add_revision, approve_print, request_proof

pytestmark = pytest.mark.django_db(transaction=True)


def revision(number=17, text="Opening day: June 12", job=None):
    with hold_nudges():
        return add_revision((job or Job.objects.create()).pk, number, text)


def published(number=18, text="New opening day: June 19"):
    r = revision(number, text)
    with hold_nudges():
        work = request_proof(r.pk)
        publish_proof.attempt_execution(work.pk)
    r.job.refresh_from_db()
    return r.job


def test_revision_and_task_roll_back_together():
    job = Job.objects.create()
    before = TaskWork.objects.count()
    with pytest.raises(ValueError), hold_nudges(), transaction.atomic():
        add_revision(job.pk, 17, "Opening day: June 12")
        raise ValueError("request rejected")
    assert not Revision.objects.filter(job=job).exists()
    assert TaskWork.objects.count() == before


def test_lost_word_count_notification_recovers():
    r = revision()
    assert r.word_count is None
    with WorkTableDelivery(registration_for(TaskWork).capability).session() as delivery:
        delivery.recover()
    r.refresh_from_db()
    assert r.word_count == 4


def test_old_proof_cannot_replace_new_proof(monkeypatch):
    old = revision()
    new = revision(18, "New opening day: June 19", old.job)
    real_render = providers.render
    overtaken = []

    def render_then_overtake(text, key, spool):
        result = real_render(text, key, spool)
        if text == old.text:
            newer = request_proof(new.pk)
            publish_proof.attempt_execution(newer.pk)
            overtaken.append(True)
        return result

    monkeypatch.setattr(providers, "render", render_then_overtake)
    with hold_nudges():
        work = request_proof(old.pk)
        publish_proof.attempt_execution(work.pk)
    old.job.refresh_from_db()
    assert overtaken == [True]
    assert providers.proof_text(old.job.published_key) == new.text


def test_late_portal_write_is_repaired(monkeypatch):
    job = published(17, "Opening day: June 12")
    old_key = job.published_key
    new = revision(18, "New opening day: June 19", job)
    real_put = providers.put_portal
    late_writes = []

    def late_put(job_id, key):
        if key == old_key and not late_writes:
            late_writes.append(key)
            next_proof = request_proof(new.pk)
            publish_proof.attempt_execution(next_proof.pk)
            project_portal.attempt_execution(PortalWork.objects.get(owner=job).pk)
        real_put(job_id, key)

    monkeypatch.setattr(providers, "put_portal", late_put)
    with hold_nudges():
        project_portal.attempt_execution(PortalWork.objects.get(owner=job).pk)
    assert providers.portal_key(job.pk) == old_key  # Fault really happened.
    with WorkTableDelivery(project_portal).session() as delivery:
        delivery.recover()
    job.refresh_from_db()
    assert late_writes == [old_key]
    assert providers.portal_key(job.pk) == job.published_key
    assert providers.proof_text(job.published_key) == new.text


def test_accepted_print_with_lost_reply_is_observed_without_resubmit():
    job = published()
    providers.lose_next_print_reply()
    with hold_nudges():
        order = approve_print(job.pk, job.published_key)
        with pytest.raises(EffectOutcomeUnknownError):
            order_printing.attempt_execution(order.printwork.pk)
    order.refresh_from_db()
    assert order.status == "unknown"
    assert providers.print_count(str(order.reference)) == 1
    with WorkTableDelivery(order_printing).session() as delivery:
        delivery.recover()
    order.refresh_from_db()
    assert order.status == "accepted"
    assert providers.print_count(str(order.reference)) == 1
    receipt = providers.observe_print(str(order.reference))
    assert receipt is not None
    assert receipt.order_id == order.provider_order_id


def test_cannot_approve_a_superseded_proof():
    job = published()
    old_key = job.published_key
    new = revision(19, "Another date", job)
    with hold_nudges():
        request_proof(new.pk)
        with pytest.raises(ValueError, match="Proof changed"):
            approve_print(job.pk, old_key)


def test_portal_handoff_commits_with_published_pointer():
    job = published()
    work = PortalWork.objects.get(owner=job)
    assert work.proof_key == job.published_key
    assert providers.portal_key(job.pk) is None  # Notification deliberately lost.
    with WorkTableDelivery(project_portal).session() as delivery:
        delivery.recover()
    assert providers.portal_key(job.pk) == job.published_key


def test_independent_cleanup_survives_process_death(settings):
    import os
    import subprocess
    import sys

    from django.db import connection

    r = revision()
    with hold_nudges():
        work = request_proof(r.pk)
    script = """
import os, django
django.setup()
from acme import providers
from acme.effects import publish_proof
render = providers.render
def render_then_die(*args):
    render(*args)
    os._exit(79)
providers.render = render_then_die
publish_proof.attempt_execution(int(os.environ["ACME_WORK_ID"]))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "project.settings",
            "PGDATABASE": connection.settings_dict["NAME"],
            "ACME_PROVIDER_DB": str(settings.PROVIDER_DB),
            "ACME_WORK_ID": str(work.pk),
        },
        timeout=30,
    )
    assert result.returncode == 79  # The child reached the intended crash point.
    cleanup = CleanupWork.objects.get()
    with providers.database() as db:
        assert db.execute("SELECT spool FROM renders WHERE key=?", (cleanup.spool_key,)).fetchone()[0] == r.text
    with WorkTableDelivery(cleanup_spool).session() as delivery:
        delivery.recover()
    with providers.database() as db:
        assert db.execute("SELECT spool FROM renders WHERE key=?", (cleanup.spool_key,)).fetchone()[0] is None
    cleanup.refresh_from_db()
    assert cleanup.state == "succeeded"
    r.job.refresh_from_db()
    assert r.job.published_key == ""
    with WorkTableDelivery(publish_proof).session() as delivery:
        delivery.recover()
    r.job.refresh_from_db()
    assert providers.proof_text(r.job.published_key) == r.text


def test_absent_spool_is_not_completion_evidence():
    assert providers.cleanup(str(uuid4())) is False
