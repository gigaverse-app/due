"""Matched, sequential HTTP workflow experiments; no throughput/load claims.

Uses separate pre-created disposable databases. Both servers run unmodified
one-second stages, through the same process supervisor and HTTP client.
"""

import argparse
import http.cookiejar
import json
import os
import platform
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql

from examples.widget_store.supervisor import supervise
from integration_tests.integration.processes import running_process

UPSTREAM_SHA = "ddaf50e71fa8e19364d9c70b5d87a9c8203af152"
ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Arm:
    name: str
    database: str
    base: str
    orders: str
    status: str
    order_id: str
    products: str

    def client(self) -> urllib.request.OpenerDirector:
        cookies = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
        with opener.open(self.base + "/", timeout=5):
            pass
        if self.name == "due":
            token = next(cookie.value for cookie in cookies if cookie.name == "csrftoken")
            assert token is not None
            opener.addheaders = [("X-CSRFToken", token)]
        return opener

    def row(self, connection: psycopg.Connection, identity: int) -> tuple[int | str, int]:
        result = connection.execute(
            sql.SQL("SELECT {}, progress_remaining FROM {} WHERE {} = %s").format(
                sql.Identifier(self.status), sql.Identifier(self.orders), sql.Identifier(self.order_id)
            ),
            (identity,),
        ).fetchone()
        assert result is not None
        return result

    def inventory(self, connection: psycopg.Connection) -> int:
        row = connection.execute(sql.SQL("SELECT inventory FROM {}").format(sql.Identifier(self.products))).fetchone()
        assert row is not None
        return row[0]

    def wait(
        self, connection: psycopg.Connection, identity: int, statuses: tuple[str | int, ...], timeout: float = 65
    ) -> tuple[str | int, int]:
        deadline = time.monotonic() + timeout
        previous = 10
        while time.monotonic() < deadline:
            status, progress = self.row(connection, identity)
            assert 0 <= progress <= previous, f"{self.name}: progress reset or duplicated"
            previous = progress
            if status in statuses:
                return status, progress
            time.sleep(0.02)
        raise AssertionError(f"{self.name}: order {identity} did not reach {statuses}")


def post(client: urllib.request.OpenerDirector, arm: Arm, path: str) -> str:
    with client.open(urllib.request.Request(arm.base + path, data=b"", method="POST"), timeout=70) as response:
        return response.read().decode()


def stats(connection: psycopg.Connection) -> dict[str, dict[str, int]]:
    connection.execute("SELECT pg_stat_clear_snapshot()")
    return {
        f"{schema}.{table}": {"inserted": inserted, "updated": updated, "deleted": deleted}
        for schema, table, inserted, updated, deleted in connection.execute(
            "SELECT schemaname, relname, n_tup_ins, n_tup_upd, n_tup_del FROM pg_stat_user_tables ORDER BY 1, 2"
        )
    }


def sample(
    arm: Arm, client: urllib.request.OpenerDirector, connection: psycopg.Connection, scenario: str
) -> dict[str, float | str | int]:
    key = str(uuid4())
    inventory = arm.inventory(connection)
    start = time.perf_counter()
    receipt = post(client, arm, f"/checkout/{key}")
    checkout_ms = (time.perf_counter() - start) * 1000
    assert post(client, arm, f"/checkout/{key}") == receipt, "duplicate submission changed receipt"
    payment_key = json.loads(receipt)["payment_id"] if arm.name == "due" else receipt
    start = time.perf_counter()
    decision = "failed" if scenario == "failed" else "paid"
    response = post(client, arm, f"/payment_webhook/{payment_key}/{decision}")
    callback_ms = (time.perf_counter() - start) * 1000
    identity = json.loads(response)["order_id"] if arm.name == "due" else int(response)
    cancelled = "cancelled" if arm.name == "due" else -1
    dispatched = "dispatched" if arm.name == "due" else 1
    paid = "paid" if arm.name == "due" else 2
    arm.wait(connection, identity, (cancelled,) if decision == "failed" else (paid, dispatched))
    decision_ms = (time.perf_counter() - start) * 1000
    recovery_start = None
    if scenario == "crash":
        deadline = time.monotonic() + 20
        while arm.row(connection, identity)[1] > 7:
            assert time.monotonic() < deadline, "dispatch never reached crash point"
            time.sleep(0.02)
        recovery_start = time.perf_counter()
        try:
            post(client, arm, "/crash_application")
        except OSError, urllib.error.URLError:
            pass  # DBOS deliberately closes its HTTP connection with os._exit.
    terminal, remaining = arm.wait(connection, identity, (cancelled,) if decision == "failed" else (dispatched,))
    total_ms = (time.perf_counter() - start) * 1000
    assert remaining == (10 if decision == "failed" else 0)
    assert arm.inventory(connection) == inventory - (decision == "paid")
    return {
        "scenario": scenario,
        "order_id": identity,
        "terminal": terminal,
        "checkout_receipt_ms": checkout_ms,
        "callback_receipt_ms": callback_ms,
        "payment_to_decision_ms": decision_ms,
        "payment_to_terminal_ms": total_ms,
        "crash_to_terminal_ms": (time.perf_counter() - recovery_start) * 1000 if recovery_start is not None else 0,
    }


def benchmark(arm: Arm, samples: int) -> dict:
    client = arm.client()
    post(client, arm, "/restock")
    with psycopg.connect(arm.database, autocommit=True) as connection:
        sample(arm, client, connection, "paid")  # same excluded warmup in both arms
        time.sleep(2)
        before = stats(connection)
        records = [sample(arm, client, connection, scenario) for scenario in ["paid"] * samples + ["failed", "crash"]]
        time.sleep(2)
        after = stats(connection)
        writes = {
            table: {kind: count - before.get(table, {}).get(kind, 0) for kind, count in values.items()}
            for table, values in after.items()
        }
    metrics = {}
    for field in ("checkout_receipt_ms", "callback_receipt_ms", "payment_to_decision_ms", "payment_to_terminal_ms"):
        values = [float(record[field]) for record in records if record["scenario"] == "paid"]
        metrics[field] = {"median": statistics.median(values), "min": min(values), "max": max(values), "n": len(values)}
    return {"samples": records, "paid_summary": metrics, "table_writes": writes}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True, help="pinned dbos-demo-apps checkout")
    parser.add_argument("--serve-dbos", action="store_true")
    parser.add_argument("--browser", action="store_true", help="run real browser crash journeys after timing")
    parser.add_argument("--due-db", default=os.environ.get("DUE_DATABASE_URL"))
    parser.add_argument("--dbos-db", default=os.environ.get("DBOS_DATABASE_URL"))
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--output", type=Path, default=Path("test-results/widgets"))
    args = parser.parse_args()
    upstream = args.upstream.resolve()
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=upstream, text=True).strip() == UPSTREAM_SHA
    assert not subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=upstream), (
        "upstream was modified"
    )
    app = upstream / "python/widget-store"
    if args.serve_dbos:
        supervise([[str(app / ".venv/bin/python"), "-m", "widget_store.main"]], app)
        return
    assert args.samples > 0 and args.due_db and args.dbos_db and args.due_db != args.dbos_db
    args.output.mkdir(parents=True, exist_ok=True)
    arms = [
        Arm(
            "due",
            args.due_db,
            "http://127.0.0.1:8001/widgets",
            "widget_store_order",
            "status",
            "id",
            "widget_store_product",
        ),
        Arm(
            "dbos",
            args.dbos_db.replace("postgresql+psycopg:", "postgresql:"),
            "http://127.0.0.1:8000",
            "orders",
            "order_status",
            "order_id",
            "products",
        ),
    ]
    results = {
        "started_utc": datetime.now(UTC).isoformat(),
        "due_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "upstream_sha": UPSTREAM_SHA,
        "python": sys.version,
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "method": "sequential HTTP; one excluded paid warmup; 10 x 1s stages; 20ms SQL observation; separate databases; full application restart, DB/broker preserved; counts include recovery and 2s observation tail; no capacity claim",
        "arms": {},
    }
    for arm in arms:
        env = {
            **os.environ,
            "DUE_DATABASE_URL": args.due_db,
            "DBOS_DATABASE_URL": args.dbos_db,
            "DJANGO_SETTINGS_MODULE": "examples.widget_store.settings",
            "DUE_QUEUE": "widget-benchmark",
        }
        command = (
            [sys.executable, "-m", "examples.widget_store.supervisor", "--port", "8001"]
            if arm.name == "due"
            else [sys.executable, "-m", "scripts.widget_benchmark", "--upstream", str(upstream), "--serve-dbos"]
        )
        with running_process(args.output, arm.name, command, env) as process:

            def ready(arm: Arm = arm) -> bool:
                try:
                    arm.client()
                    return True
                except OSError, urllib.error.URLError:
                    return False

            process.wait_until(ready, timeout=60)
            results["arms"][arm.name] = benchmark(arm, args.samples)
            print(f"{arm.name}: {results['arms'][arm.name]['paid_summary']}", flush=True)
            if args.browser:
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "scripts.widget_browser",
                        arm.name,
                        "--url",
                        arm.base + "/",
                        "--output",
                        str(args.output),
                    ],
                    check=True,
                    timeout=150,
                )
        (args.output / "results.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
