"""Resource counters for isolated, sequential Widget Store benchmark arms.

SQL counters exclude the observer connection. Container CPU includes the same
20ms observation traffic in both arms; process CPU is sampled, not billing data.
"""

import os
import subprocess
from threading import Event, Thread

import psutil
import psycopg
from psycopg.rows import dict_row
from redis import Redis

SQL_FIELDS = (
    "calls",
    "total_exec_time",
    "shared_blks_hit",
    "shared_blks_read",
    "shared_blks_dirtied",
    "shared_blks_written",
    "temp_blks_read",
    "temp_blks_written",
    "wal_records",
    "wal_fpi",
    "wal_bytes",
    "shared_blk_read_time",
    "shared_blk_write_time",
)
REDIS_FIELDS = (
    "total_commands_processed",
    "total_net_input_bytes",
    "total_net_output_bytes",
    "used_cpu_sys",
    "used_cpu_user",
)


def sql_counters(connection: psycopg.Connection) -> dict[str, dict[str, float]]:
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT userid::text || ':' || queryid::text AS identity, query, "
            + ", ".join(SQL_FIELDS)
            + " FROM pg_stat_statements WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())"
        )
        return {row.pop("identity"): row for row in cursor}


def redis_counters(client: Redis) -> dict[str, float]:
    info = client.info()
    assert isinstance(info, dict)
    return {field: float(info[field]) for field in REDIS_FIELDS}


def container_cpu(container: str) -> float:
    # Dedicated benchmark containers only, supplied explicitly by the runner.
    output = subprocess.check_output(["docker", "exec", container, "cat", "/sys/fs/cgroup/cpu.stat"], text=True)
    counters = dict(line.split() for line in output.splitlines())
    return int(counters["usage_usec"]) / 1_000_000


class Resources:
    def __init__(self, connection: psycopg.Connection, pid: int) -> None:
        self.connection = connection
        self.root = psutil.Process(pid)
        self.samples: dict[tuple[int, float], float] = {}
        self.commands: dict[tuple[int, float], str] = {}
        self.phases: list[dict] = []
        self.stop = Event()
        self.thread = Thread(target=self.sample, daemon=True)
        self.containers = {
            name: os.environ[f"WIDGET_{name.upper()}_CONTAINER"]
            for name in ("postgres", "redis")
            if os.environ.get(f"WIDGET_{name.upper()}_CONTAINER")
        }
        self.redis = Redis.from_url(os.environ["DUE_BROKER_URL"])

    def read_processes(self) -> None:
        for process in (self.root, *self.root.children(recursive=True)):
            try:
                cpu = process.cpu_times()
                key = (process.pid, process.create_time())
                if key not in self.commands:
                    self.commands[key] = " ".join(process.cmdline())
                self.samples[key] = cpu.user + cpu.system
            except psutil.NoSuchProcess:
                pass

    def process_cost(self) -> dict[str, float]:
        commands: dict[str, float] = {}
        for key, value in self.samples.copy().items():
            command = self.commands[key]
            commands[command] = commands.get(command, 0) + value - self.process_before.get(key, 0)
        return commands

    def mark(self, label: str) -> None:
        """Cumulative sampled process CPU; differences separate warm work from restart."""
        self.read_processes()
        self.phases.append({"after": label, "process_cpu_seconds": self.process_cost()})

    def sample(self) -> None:
        while not self.stop.wait(0.01):
            self.read_processes()

    def __enter__(self):
        self.sql_before = sql_counters(self.connection)
        self.cpu_before = {name: container_cpu(container) for name, container in self.containers.items()}
        self.redis_before = redis_counters(self.redis)
        self.read_processes()
        self.process_before = self.samples.copy()
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop.set()
        self.thread.join()
        self.read_processes()
        self.cpu = {
            name: container_cpu(container) - self.cpu_before[name] for name, container in self.containers.items()
        }
        self.redis_after = redis_counters(self.redis)
        self.sql_after = sql_counters(self.connection)
        self.redis.close()

    def result(self) -> dict:
        statements = [
            {
                "query": row["query"],
                **{
                    field: float(row[field]) - float(self.sql_before.get(key, {}).get(field, 0)) for field in SQL_FIELDS
                },
            }
            for key, row in self.sql_after.items()
        ]
        statements = [row for row in statements if row["calls"]]
        return {
            "sql": {field: sum(row[field] for row in statements) for field in SQL_FIELDS},
            "statements": sorted(statements, key=lambda row: row["total_exec_time"], reverse=True),
            "container_cpu_seconds": self.cpu,
            "process_cpu_seconds": self.process_cost(),
            "cumulative_phase_cpu": self.phases,
            "sampled_application_cpu_seconds": sum(
                value - self.process_before.get(key, 0) for key, value in self.samples.items()
            ),
            "redis": {field: self.redis_after[field] - self.redis_before[field] for field in REDIS_FIELDS},
            "scope": "Application SQL excludes observer session; database-wide counts include recovery/startup after crash. Container CPU includes matched observer queries, infrastructure background work and instrumentation. Application CPU sampled every 10ms across supervisor descendants, including restarts; may miss CPU at process exit. Redis counters include INFO sampling and idle housekeeping. No idle subtraction, capacity or monetary-cost inference.",
        }
