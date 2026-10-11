"""SQL statement/row accounting; real PostgreSQL, deterministic broker seam, not a latency benchmark."""

# ruff: noqa: E402
import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

p = argparse.ArgumentParser()
p.add_argument("--repo", type=Path, required=True)
p.add_argument("--database-url", required=True)
p.add_argument("--output", type=Path, required=True)
p.add_argument("--submission-keys", action="store_true")
a = p.parse_args()
sys.path[:0] = [str(a.repo / "src"), str(a.repo)]
os.environ.update(
    DJANGO_SETTINGS_MODULE="examples.settings",
    DUE_DATABASE_URL=a.database_url,
    DUE_OUTPUT_DIR=str(a.output.parent / ("sql-files-" + a.output.stem)),
)
import django

django.setup()
import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from due.django.conformance.background import accounted_background_work
from due.django.models import EffectAttempt, RecoveryDeferral
from examples.scheduled.effects import publish_at_deadline
from examples.scheduled.models import Publication, PublicationDeadline
from examples.scheduled.services import create_publication, reschedule_publication

call_command("migrate", verbosity=0)
result = {}


def measure(name, operation):
    with CaptureQueriesContext(connection) as q:
        value = operation()
    result[name] = {
        "statement_counts": dict(Counter(x["sql"].split()[0] for x in q)),
        "sql": [x["sql"] for x in q],
        "publication_rows": Publication.objects.count(),
        "work_rows": PublicationDeadline.objects.count(),
    }
    return value


with pytest.MonkeyPatch.context() as patch, accounted_background_work(patch) as driver:
    key, at = uuid4(), timezone.now() + timedelta(seconds=30)
    row = measure(
        "create",
        lambda: create_publication("SQL accounting", at, **({"submission_key": key} if a.submission_keys else {})),
    )
    if a.submission_keys:
        replay = measure("replay", lambda: create_publication("SQL accounting", at, submission_key=key))
        assert replay.pk == row.pk
    measure("reschedule", lambda: reschedule_publication(row.pk, timezone.now() + timedelta(seconds=3)))
    measure("lookahead", publish_at_deadline.recover)
    work = row.publicationdeadline
    hints = [d for d in driver.dispatches if d.name.endswith("wake_scheduled_work_task")]
    # Let the real DB clock pass the deadline, but keep queued hints held until explicit delivery.
    time.sleep(3.1)
    if hints:
        measure("execution", lambda: driver.deliver(hints[0]))
        measure("duplicate_hint", lambda: driver.deliver(hints[1]))
    else:
        measure("execution", lambda: publish_at_deadline.enqueue_execution(work.pk))
    row.refresh_from_db()
    assert row.status == Publication.Status.PUBLISHED
    result["messages"] = dict(Counter(d.name for d in driver.dispatches))
    result["final_rows"] = {
        "publication": Publication.objects.count(),
        "work": PublicationDeadline.objects.count(),
        "attempt": EffectAttempt.objects.count(),
        "deferral": RecoveryDeferral.objects.count(),
    }
a.output.write_text(json.dumps(result, indent=2))
print(a.output)
