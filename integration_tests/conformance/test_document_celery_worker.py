"""
A document publication through a real Celery worker that fails at each of Celery's stages, recovered by Due.

``celery_worker_contract`` generates the histories: the producer records the publication, the worker runs it
and fails (its pool child lost as the task starts, after the work ran, or after Celery stored the result, also by
SIGKILL; or the task's ``on_success`` hook raising), the worker starts again, and Due's recovery runs. Every history
must publish the document once, as a run with no failure does. The last test is the control: the same histories
without Due's recovery lose the publication whose delivery died as it started, as Celery alone does.
"""

import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import connection
from pytest_obligation import due_work_contract_suite
from pytest_obligation.crash_histories import HistoriesDiverged
from pytest_obligation.integrations.celery_tasks import task_contract
from pytest_obligation.process_histories import assert_process_deaths_converge

from due.django.conformance import celery_worker_contract
from due.django.runtime.names import RUN_TASK
from examples.documents.effects import request_publication
from examples.documents.models import Document, PublicationWork

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="Celery's prefork pool does not run on Windows; running_worker refuses it"
)

APP = "examples.celery_app:app"
#: Where the worker writes published documents, shared by every history in this process.
OBJECTS = Path(tempfile.mkdtemp(prefix="due-celery-crashes-")) / "objects"


def worker_environment() -> dict[str, str]:
    """The worker's database is this test's, whose name pytest-django chose when the test began."""
    base = os.environ.get("DUE_DATABASE_URL", "postgresql://due:due@127.0.0.1:55439/due")
    return {
        "DUE_DATABASE_URL": base.rsplit("/", 1)[0] + "/" + str(connection.settings_dict["NAME"]),
        "DUE_OUTPUT_DIR": str(OBJECTS),
    }


def publish_a_document() -> PublicationWork:
    # ARRANGE: a document no history has published.
    document = Document.objects.create()
    # REAL PRODUCTION: the producer commits the text and its publication together; the wake-up follows the commit.
    return request_publication(document.pk, f"revision {uuid4()}")


def published(work: PublicationWork) -> bool:
    # OBSERVE: the document points at a published object holding exactly its text.
    key = Document.objects.get(pk=work.owner_id).published_key
    return bool(key) and (OBJECTS / key).read_text() == work.text


PUBLICATION = celery_worker_contract(
    "publish_document",
    app=APP,
    send=publish_a_document,
    effect=published,
    initial_effect=False,
    expected_effect=True,
    env=worker_environment,
)


@due_work_contract_suite(PUBLICATION)
class TestPublicationThroughCelery:
    """Every case is generated; each failure converges on one published document, with nothing declared."""


@pytest.mark.django_db(transaction=True)
def test_without_due_recovery_a_delivery_lost_as_it_starts_is_lost() -> None:
    celery_alone = task_contract(
        APP,
        RUN_TASK,
        send=publish_a_document,
        effect=published,
        initial_effect=False,
        expected_effect=True,
        env=worker_environment,
        name="a worker runs 'publish_document' through Celery, with Due's recovery unbound",
    )
    (history,) = celery_alone.process_handoffs
    with pytest.raises(HistoriesDiverged, match="died at task_prerun"):
        assert_process_deaths_converge(history)
