"""
Operator inspection must show real failures without changing their state.

Design and usage: docs/operations.md.
"""

import json
from contextlib import suppress
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from due.django.testing import make_due
from tests.harness.primitive import (
    PrimitiveWork,
    primitive,
    record,
)
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("isolated_primitive")]


def test_inspection_lists_failed_work_but_never_rearms_it_or_exposes_payload():
    work = record(payload="private application input")
    with patch("tests.harness.primitive.call", side_effect=RuntimeError("provider unavailable")):
        assert primitive.policy.max_rearms is not None
        for _ in range(primitive.policy.max_rearms + 1):
            make_due(work)
            with suppress(Exception):
                primitive.attempt_execution(work.pk)
    before = PrimitiveWork.objects.get(pk=work.pk)
    output = StringIO()
    call_command("due_status", "primitive_self_test", limit=1, stdout=output)
    result = json.loads(output.getvalue())
    assert result["rows"][0]["id"] == work.pk
    assert result["rows"][0]["state"] == "exhausted"
    assert "private application input" not in output.getvalue()
    work.refresh_from_db()
    assert (work.state, work.fencing_token, work.desired_revision) == (
        before.state,
        before.fencing_token,
        before.desired_revision,
    )


@pytest.mark.parametrize("args", [("missing",), ("--limit", "0"), ("--limit", "1001")])
def test_inspection_rejects_unknown_route_and_unbounded_limit(args):
    with pytest.raises(CommandError):
        call_command("due_status", *args)


def test_inspection_lists_identities_recovery_is_holding_out():
    from due.django.models import RecoveryDeferral
    from due.django.runtime.metrics import RecoveryPhase
    from due.django.runtime.recovery_pass import defer_identity

    defer_identity(PrimitiveWork._meta.label_lower, RecoveryPhase.REAP, "42", "RuntimeError('finalizer failed')")
    output = StringIO()
    call_command("due_status", "primitive_self_test", stdout=output)
    [hold] = json.loads(output.getvalue())["recovery_deferrals"]
    assert (hold["phase"], hold["identity"], hold["failures"]) == ("reap", "42", 1)
    assert "last_error" not in hold  # Exception text can contain application secrets.
    assert RecoveryDeferral.objects.count() == 1  # Read-only: inspection does not clear or extend the hold.
