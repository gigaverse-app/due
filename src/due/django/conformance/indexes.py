"""Cheap catalog proof for every enrolled work model; no history fixture required."""

from django.db import connection

# pytest-global: All enrolled models reuse the existing root-owned index-eligibility verdict.
from pytest_obligation.integrations.django.selection import DjangoSelectionInspector

# pytest-global: Optional observer schema shares the generated catalog proof.
from due.django.capabilities.observed_operations import (
    ObservedExternalOperationWork,
    observation_index,
)

# pytest-global: Generated catalog checks for every enrolled capability share the canonical schema and state domain.
from due.django.models import (
    DurableWorkState,
    UniverseDurableWork,
    durable_work_indexes,
)


# pytest-global: Every enrolled capability receives this migrated-schema assertion.
def assert_work_indexes(model: type[UniverseDurableWork]) -> None:
    """
    Check installed indexes, not just Meta, against the shared schema contract.

    Names may differ across adopters. Predicate truth is compared over the whole
    state domain, NULL and an unrecognized state; PostgreSQL owns SQL parsing, so
    equivalent IN/ANY syntax and casts do not cause spurious failures.
    """
    table = model._meta.db_table
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT i.indnkeyatts, i.indnatts,
                   ARRAY(SELECT pg_get_indexdef(i.indexrelid, n, true)
                         FROM generate_series(1, i.indnatts) n),
                   pg_get_expr(i.indpred, i.indrelid), c.relname
            FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
            JOIN pg_am am ON am.oid = c.relam
            WHERE i.indrelid = %s::regclass AND i.indisvalid AND i.indisready
                  AND am.amname = 'btree'
            """,
            [table],
        )
        installed = cursor.fetchall()
        states = [*DurableWorkState.values, None, "unrecognized"]
        values = ", ".join(["(%s::varchar)"] * len(states))
        expected_indexes = durable_work_indexes(name_prefix="contract")
        if issubclass(model, ObservedExternalOperationWork):
            expected_indexes.append(observation_index("contract_observe_ix"))
        for expected in expected_indexes:
            # The schema factory owns the predicate. Compile it with the same
            # Django backend migrations use; only the installed side is catalog data.
            assert expected.condition is not None
            query = model.objects.filter(expected.condition).query
            condition, params = query.where.as_sql(query.get_compiler(connection=connection), connection)
            columns = [*expected.fields, *expected.include]
            candidates = [
                (predicate, name)
                for key_count, total, actual_columns, predicate, name in installed
                if key_count == len(expected.fields) and total == len(columns) and actual_columns == columns
            ]
            matched: set[str] = set()
            verified_predicates: set[str] = set()
            for predicate, name in candidates:
                cursor.execute(
                    f"SELECT bool_and((({condition}) IS TRUE) = (({predicate or 'TRUE'}) IS TRUE)) "
                    f"FROM (VALUES {values}) AS {quote(table)}(state)",
                    [*params, *states],
                )
                if cursor.fetchone()[0]:
                    matched.add(name)
                    verified_predicates.add(predicate)
            assert matched, f"{table}: missing valid shared index {expected.name} ({columns}, WHERE {condition})"

            # Truth on sample values is insufficient: lower(state) may agree on
            # every enum value but cannot serve the runtime's state predicate.
            # This is eligibility only (EXPLAIN without ANALYZE and no fixtures),
            # reusing Profile A's structural verdict. The separate scale suite
            # measures cost with normal planner settings. Restore even inside
            # an enclosing test transaction so this probe cannot bias that suite.
            queryset = model.objects.filter(expected.condition).values(*columns).order_by(*expected.fields)
            # The schema still requires the exact shared keys above. PostgreSQL
            # may serve this selection through a different key layout, however.
            # Trust that partial bitmap only when its catalog-normalized WHERE
            # exactly matches a predicate we validated, never by index name alone.
            predicate_indexes = {name for _, _, _, predicate, name in installed if predicate in verified_predicates}
            served, explanation = DjangoSelectionInspector(partial_indexes=predicate_indexes).index_served(queryset)
            assert served, f"{table}: {expected.name}: {explanation}"
