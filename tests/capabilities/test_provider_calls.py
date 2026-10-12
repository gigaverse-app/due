"""
Real projection execution commits evidence before an interruptible provider call.

Design and proof boundaries: docs/architecture.md.
See the guide's verification module map for related production-bound suites.
"""

from typing import ClassVar

import pytest
from django.db import connection

from due.django.capabilities.projection import DurableProjection
from due.django.evidence.attempts import attempts_for
from due.django.models import EffectAttemptOutcome
from due.django.public import DurableWorkPolicy
from due.django.testing import WorkerDied
from due.errors import EffectNotAppliedError
from tests.harness.primitive import (
    PrimitiveOwner,
    PrimitiveWork,
    record,
)
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)


class Projection(DurableProjection[PrimitiveOwner, PrimitiveWork]):
    failure: ClassVar[type[BaseException] | None] = None

    def write_projection(self, work: PrimitiveWork) -> None:
        assert not connection.in_atomic_block
        assert attempts_for(work).get().outcome == EffectAttemptOutcome.UNKNOWN
        if self.failure is not None:
            raise self.failure("provider interrupted")


projection = Projection.for_owner(
    owner=PrimitiveOwner,
    work=PrimitiveWork,
    policy=DurableWorkPolicy(retention=None),
)


@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize(
    ("failure", "outcome"),
    [
        (None, EffectAttemptOutcome.APPLIED),
        (EffectNotAppliedError, EffectAttemptOutcome.NOT_APPLIED),
        (OSError, EffectAttemptOutcome.UNKNOWN),
        (WorkerDied, EffectAttemptOutcome.UNKNOWN),
    ],
)
def test_only_definite_provider_observations_replace_unknown(
    failure: type[BaseException] | None, outcome: EffectAttemptOutcome, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = record()
    monkeypatch.setattr(Projection, "failure", failure)
    if failure is None:
        assert projection.attempt_execution(work.pk)
    else:
        with pytest.raises(failure, match="provider interrupted"):
            projection.attempt_execution(work.pk)
    assert attempts_for(work).get().outcome == outcome
