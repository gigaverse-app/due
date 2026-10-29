"""
Opt-in PostgreSQL measurement; run explicitly with pytest on a disposable test DB.

Set MAIL_BENCH_ENGINE=native|due, MAIL_BENCH_BLOCK to the arm label, and
MAIL_BENCH_OUTPUT to an ignored JSONL path. Run serially with identical source,
fixtures, shortlink behavior and harness on every comparison arm. Fixture setup,
verification and output are outside timing. One warmup precedes each scenario.
Records SQL (without parameters), DML statements, explicit commits and elapsed
milliseconds for campaign construction and delivery separately. Fake SES provides
identical responses; this measures persistence overhead, not production throughput.
"""

import hashlib
import json
import os
import subprocess
import time
from collections import Counter
from contextlib import nullcontext
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
import psutil
from django.db import connection
from django.db.backends.postgresql.base import DatabaseWrapper

from gigaverse.core.account.tests.factories import UserFactory
from gigaverse.core.mail.reminders.engine.cohort import build_campaign
from gigaverse.core.mail.reminders.engine.dispatch import ThrottlePacing, dispatch_campaign_once
from gigaverse.core.mail.reminders.engine.tests.harness.fake_ses import (
    PERMANENT_SUBSET,
    SUCCESS,
    THROTTLE_SUBSET,
    Directive,
    ScriptedSesClient,
)
from gigaverse.core.mail.reminders.sender_mode import SenderMode
from gigaverse.core.mail.reminders.tests.stubs import StubKillSwitch
from gigaverse.core.mail.tests.ses_fixtures import unprefixed_ses_settings_fixture as unprefixed_ses_settings_fixture
from gigaverse.core.payments.tests.large_event import make_large_event

pytestmark = pytest.mark.django_db(transaction=True)


class Measurement:
    def __init__(self):
        self.statements = []
        self.commits = 0
        self.commit_ms = 0.0
        self.autocommit_statements = 0

    def execute(self, execute, sql, params, many, context):
        self.autocommit_statements += int(connection.get_autocommit() and not connection.in_atomic_block)
        started = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            self.statements.append({"sql": sql, "ms": (time.perf_counter() - started) * 1000})

    def run(self, operation):
        commit = connection.commit

        def measured_commit():
            started = time.perf_counter()
            try:
                return commit()
            finally:
                self.commits += 1
                self.commit_ms += (time.perf_counter() - started) * 1000

        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
            cursor.execute("SELECT pg_backend_pid()")
            backend = psutil.Process(cursor.fetchone()[0])
            cursor.execute("SELECT pg_stat_statements_reset()")
        db_before = backend.cpu_times()
        cpu_started = time.process_time()
        started = time.perf_counter()
        with connection.execute_wrapper(self.execute), patch.object(connection, "commit", measured_commit):
            result = operation()
        elapsed = (time.perf_counter() - started) * 1000
        cpu_ms = (time.process_time() - cpu_started) * 1000
        db_after = backend.cpu_times()
        db_cpu_ms = ((db_after.user + db_after.system) - (db_before.user + db_before.system)) * 1000
        with connection.cursor() as cursor:
            cursor.execute("""SELECT coalesce(sum(total_plan_time), 0), coalesce(sum(total_exec_time), 0),
                coalesce(sum(shared_blks_hit), 0), coalesce(sum(shared_blks_read), 0),
                coalesce(sum(shared_blks_dirtied), 0), coalesce(sum(wal_records), 0), coalesce(sum(wal_bytes), 0),
                coalesce(sum(calls), 0), coalesce(sum(rows), 0)
                FROM pg_stat_statements WHERE dbid=(SELECT oid FROM pg_database WHERE datname=current_database())
                AND query NOT LIKE 'SELECT pg_stat_statements_reset%'""")
            values = cursor.fetchone()
        db = dict(zip(('plan_ms', 'exec_ms', 'buffer_hits', 'buffer_reads', 'buffers_dirtied', 'wal_records', 'wal_bytes', 'calls', 'rows'), map(float, values)))
        counts = Counter(item["sql"].split()[0].upper() for item in self.statements)
        return result, {
            "elapsed_ms": elapsed,
            "cpu_ms": cpu_ms,
            "db_cpu_ms": db_cpu_ms,
            "server_transactions": self.commits + self.autocommit_statements,
            "db": db,
            "sql_ms": sum(item["ms"] for item in self.statements),
            "statements": len(self.statements),
            "kinds": dict(counts),
            "writes": sum(counts[k] for k in ("INSERT", "UPDATE", "DELETE")),
            "commits": self.commits,
            "commit_ms": self.commit_ms,
            "trace": self.statements,
        }


@pytest.mark.parametrize(
    ("tickets", "outcome"), [(50, "success"), (200, "success"), (400, "success")]
)
def test_measure_mail(tickets, outcome):
    engine = os.environ["MAIL_BENCH_ENGINE"]
    assert engine in {"native", "due"}
    if engine == "due":
        from gigaverse.core.mail.reminders.engine.tests.harness.delivery_engine import (
            DeliveryEngine,
            delivering_through,
        )

        context = partial(delivering_through, DeliveryEngine.DUE)
    else:
        context = nullcontext
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    import due

    tracked_diff = subprocess.check_output(["git", "diff", "HEAD", "--binary"])
    provenance = {
        "dirty": bool(tracked_diff),
        "diff_sha256": hashlib.sha256(tracked_diff).hexdigest(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "postgres_version": cast(DatabaseWrapper, connection).pg_version,
    }
    if os.environ.get("MAIL_BENCH_REQUIRE_CLEAN", "1") == "1":
        assert not tracked_diff, "commit the measured source, or explicitly label a dirty diagnostic run"
    for iteration in range(int(os.environ.get("MAIL_BENCH_SAMPLES", "5")) + 1):
        fixture = make_large_event(ticket_count=tickets, with_participants=False)
        event = fixture.event
        event.creator = UserFactory.create()
        event.email_sender_mode = SenderMode.BULK
        event.save(update_fields=["creator", "email_sender_mode"])
        with context():
            campaign, admission = Measurement().run(lambda: build_campaign(event))
        chunks = list(campaign.chunks.order_by("seq"))
        attendee = next(chunk for chunk in chunks if chunk.recipients[0]["r"] == "attendee")
        directive = {
            "success": Directive(SUCCESS),
            "throttle": Directive(THROTTLE_SUBSET, 7),
            "permanent": Directive(PERMANENT_SUBSET, 7),
        }[outcome]
        client = ScriptedSesClient(script={attendee.seq: [directive]}, chunk_seq_of={str(c.pk): c.seq for c in chunks})
        _, delivery = Measurement().run(
            lambda: dispatch_campaign_once(
                campaign.pk,
                client=client,
                kill_switch=StubKillSwitch(paused=False),
                pacing=ThrottlePacing(backoff_base_seconds=0),
                sleep=lambda _: None,
            )
        )
        campaign.refresh_from_db()
        event.refresh_from_db()
        states = Counter(e["s"] for c in campaign.chunks.all() for e in c.recipients)
        accepted = tickets + 1 - (7 if outcome == "permanent" else 0)
        assert sum(client.sent_counts().values()) == accepted
        assert all(count == 1 for count in client.sent_counts().values())
        assert campaign.status == "COMPLETED" and event.email_notification_status == "SENT"
        assert states["ACCEPTED"] == accepted
        assert states["FAILED_PERMANENT"] == (7 if outcome == "permanent" else 0)
        assert states["UNKNOWN"] == 0
        row = {
            "utc": datetime.now(UTC).isoformat(),
            "source": source,
            "engine": engine,
            "due_module": str(due.__file__),
            "provenance": provenance,
            "block": os.environ["MAIL_BENCH_BLOCK"],
            "tickets": tickets,
            "outcome": outcome,
            "iteration": iteration,
            "warmup": iteration == 0,
            "chunks": len(chunks),
            "provider_calls": len(client.calls),
            "batches": [len(call.keys) for call in client.calls],
            "accepted": accepted,
            "admission": admission,
            "delivery": delivery,
        }
        with Path(os.environ["MAIL_BENCH_OUTPUT"]).open("a") as file:
            file.write(json.dumps(row) + "\n")
