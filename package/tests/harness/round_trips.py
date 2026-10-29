"""
Name every database round trip a durable-work path sends, by what it does to which table.

Budgets compare these names exactly, so a returning redundant read, a separate
clock read or a savepoint fails where it is introduced rather than showing up
as database load. Transaction control (BEGIN/COMMIT) is the same before and
after an optimization and is left out; ROLLBACK is named, because a path that
declines and retries another way pays for it. Anything else unclassified fails.
"""

from collections.abc import Callable
from dataclasses import dataclass

from django.db import connection, models
from django.test.utils import CaptureQueriesContext

from due.django.models import RecoveryDeferral

CLOCK = "clock"
HOLDS = "read holds"
HOLD_CENSUS = "count holds"
READ_WORK = "read work"
COUNT_WORK = "count work"
LOCK_WORK = "lock work"
UPDATE_WORK = "update work"
DELETE_WORK = "delete work"
READ_OWNER = "read owner"
COUNT_OWNER = "count owner"
LOCK_OWNER = "lock owner"
UPDATE_OWNER = "update owner"
ROLLBACK = "rollback"
SAVEPOINT = "savepoint"
RELEASE_SAVEPOINT = "release savepoint"


@dataclass(frozen=True)
class RoundTrips:
    """The statement names of one work table and its owner."""

    work: type[models.Model]
    owner: type[models.Model]

    def shape(self, sql: str) -> str:
        """What a statement does, by the table it targets: enough to name every round trip of these paths."""
        work, owner, holds = (model._meta.db_table for model in (self.work, self.owner, RecoveryDeferral))
        if sql == "SELECT clock_timestamp()":
            return CLOCK
        if sql == "ROLLBACK":
            return ROLLBACK
        if sql.startswith("SAVEPOINT "):
            return SAVEPOINT
        if sql.startswith("RELEASE SAVEPOINT "):
            return RELEASE_SAVEPOINT
        for verb, table, shape in (
            ("UPDATE", work, UPDATE_WORK),
            ("UPDATE", owner, UPDATE_OWNER),
            ("DELETE FROM", work, DELETE_WORK),
        ):
            if sql.startswith(f'{verb} "{table}"'):
                return shape
        # The outermost FROM: an owner lock names the work table only in its sub-select.
        target = sql.split(" FROM ", 1)[1].split()[0].strip('"') if sql.startswith("SELECT ") else ""
        counted = "GROUP BY" in sql or "COUNT(" in sql
        if target == holds:
            return HOLD_CENSUS if counted else HOLDS
        if target == work:
            return LOCK_WORK if " FOR UPDATE" in sql else COUNT_WORK if counted else READ_WORK
        if target == owner:
            return LOCK_OWNER if " FOR UPDATE" in sql else COUNT_OWNER if counted else READ_OWNER
        raise AssertionError(f"unclassified statement: {sql}")

    def of(self, run: Callable[[], object]) -> list[str]:
        """Every statement ``run`` sends, in order, without transaction control."""
        with CaptureQueriesContext(connection) as captured:
            run()
        return [
            self.shape(query["sql"]) for query in captured.captured_queries if query["sql"] not in ("BEGIN", "COMMIT")
        ]
