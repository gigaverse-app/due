"""A real replay-safe handler that inspects readiness before calling its provider."""

from datetime import timedelta

from due.django.runtime.coordination import ContinueAfter, WorkExecutionContext, database_now
from tests.harness.primitive import PrimitiveEffect, PrimitiveOwner, PrimitiveWork


class GatedPrimitiveEffect(PrimitiveEffect):
    def apply(self, work: PrimitiveWork, context: WorkExecutionContext[PrimitiveWork]) -> str | ContinueAfter:
        if PrimitiveOwner.objects.get(pk=work.owner_id).value != "ready":
            return ContinueAfter(timedelta(seconds=30), until=database_now() + timedelta(hours=1))
        return super().apply(work, context)
