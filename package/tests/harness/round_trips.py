"""
Name every database round trip a durable-work path sends, by what it does to which table's role.

Budgets compare these names in order, so a returning redundant read, a separate
clock read or a savepoint fails where it is introduced rather than showing up as
database load. Statements are named by pytest-obligation's classifier, the one
the generated cost contracts use; this harness only maps a capability's own
tables to roles (work, owner, holds), so one budget reads the same for every
model. Transaction control (BEGIN/COMMIT) is the same before and after an
optimization and is left out; ROLLBACK and savepoints are named, because a path
that pays for them is doing more work. Anything else fails.

The generated contracts compare a multiset; these budgets keep the order where
the order is the point, such as a clock read after a lock.
"""

from collections.abc import Callable
from dataclasses import dataclass

from django.db import connection, models
from django.test.utils import CaptureQueriesContext
from pytest_obligation.costs import statement_shape

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

_UNTABLED = {
    "select clock_timestamp()": CLOCK,
    "rollback": ROLLBACK,
    "savepoint": SAVEPOINT,
    "release savepoint": RELEASE_SAVEPOINT,
}
_HOLD_VERBS = {"read": HOLDS, "count": HOLD_CENSUS}


@dataclass(frozen=True)
class RoundTrips:
    """The statement names of one work table and its owner."""

    work: type[models.Model]
    owner: type[models.Model]

    def shape(self, sql: str) -> str:
        """What a statement does, to which of these tables' roles; a statement outside them fails."""
        named = statement_shape(sql, strict=True)
        if named in _UNTABLED:
            return _UNTABLED[named]
        verb, _, table = named.partition(" ")
        if table == RecoveryDeferral._meta.db_table and verb in _HOLD_VERBS:
            return _HOLD_VERBS[verb]
        role = {self.work._meta.db_table: "work", self.owner._meta.db_table: "owner"}.get(table)
        if role is None:
            raise AssertionError(f"unclassified statement: {sql}")
        return f"{verb} {role}"

    def of(self, run: Callable[[], object]) -> list[str]:
        """Every statement ``run`` sends, in order, without transaction control."""
        with CaptureQueriesContext(connection) as captured:
            run()
        return [
            self.shape(query["sql"]) for query in captured.captured_queries if query["sql"] not in ("BEGIN", "COMMIT")
        ]
