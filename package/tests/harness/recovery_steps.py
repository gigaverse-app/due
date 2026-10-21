"""Real PostgreSQL recovery adopters and deliberate fault controls for coverage checks."""

from contextlib import contextmanager
from dataclasses import replace

from django.conf import settings
from django.test import override_settings

from due.django.conformance.examples import EffectExamples
from due.django.conformance.recovery_steps import RecoveryStepBinding, RecoveryStepExample, poison_recovery_call
from due.django.models import RecoveryDeferral
from due.django.public import database_now
from due.django.runtime.metrics import RecoveryPhase
from due.django.runtime.recovery_pass import RecoveryPass, RecoveryStep
from due.django.runtime.registry import DurableWorkRegistration, recovery_schedules
from tests.harness.enrollment import no_fixture
from tests.harness.primitive import PrimitiveEffect, PrimitiveOwner, PrimitiveWork, primitive, primitive_tables
from tests.harness.registrations import durable_work_installed


def collection_only_replay():
    # ARRANGE: metadata-only suite construction; no product fixture may run.
    # REAL PRODUCTION: the real suite compiler and collection guard run in the caller.
    # EXTERNAL SEAM: none; executing this placeholder is itself a test failure.
    # OBSERVE: collection must reject removed generated proofs before execution.
    return no_fixture()


class Cleanup(PrimitiveEffect):
    def derivation_steps(self) -> tuple[RecoveryStep, ...]:
        return (*super().derivation_steps(), RecoveryStep(RecoveryPhase.DISCOVERY, self.cleanup))

    def cleanup(self, recovery: RecoveryPass) -> int:
        return recovery.run_page(RecoveryPhase.DISCOVERY, self.candidates(), self.policy.batch_size, self.process)

    def candidates(self):
        return PrimitiveOwner.objects.filter(value="pending").order_by("pk").values_list("pk", flat=True)

    def process(self, identity):
        return PrimitiveOwner.objects.filter(pk=identity).update(value="done")

    def unrelated(self, identity):
        return 0


class UnfairCleanup(Cleanup):
    def cleanup(self, recovery: RecoveryPass) -> int:
        changed = 0
        for identity in self.candidates()[: self.policy.batch_size]:
            with recovery.isolate(RecoveryPhase.DISCOVERY, identity):
                changed += self.process(identity)
        return changed


class PagedCleanup(Cleanup):
    def derivation_steps(self) -> tuple[RecoveryStep, ...]:
        return (
            RecoveryStep.paged(
                RecoveryPhase.DISCOVERY,
                self.candidates,
                lambda identity: self.process(identity),
                size=self.policy.batch_size,
            ),
        )


class NoopCleanup(Cleanup):
    def cleanup(self, recovery: RecoveryPass) -> int:
        return 0


class AbandoningCleanup(Cleanup):
    def candidates(self):
        # Deliberately wrong: permanently excludes failed work when its hold
        # expires. Healthy work passes, but the preserved obligation is stranded.
        held = RecoveryDeferral.objects.filter(effect=self.work_name, retry_at__lt=database_now()).values_list(
            "identity", flat=True
        )
        PrimitiveOwner.objects.filter(pk__in=list(held)).update(claimed_at=database_now())
        return super().candidates().filter(claimed_at__isnull=True)


class DelegatingReap(PrimitiveEffect):
    """An override that still reaches the library's claim settlement."""

    def _reap(self, recovery: RecoveryPass) -> int:
        return super()._reap(recovery)


class SkippingReap(PrimitiveEffect):
    """Deliberately wrong: an override that never settles an expired claim."""

    def _reap(self, recovery: RecoveryPass) -> int:
        return 0


@contextmanager
def _installed(capability, name):
    entry = DurableWorkRegistration(name=name, capability=capability)
    with (
        primitive_tables(),
        durable_work_installed(entry),
        override_settings(CELERY_BEAT_SCHEDULE={**settings.CELERY_BEAT_SCHEDULE, **recovery_schedules([entry])}),
    ):
        yield


@contextmanager
def cleanup_binding(implementation=Cleanup, *, seam=None):
    """``seam=None`` uses the default fault at ``run_page``; a method name poisons that call instead."""
    capability = implementation.for_owner(
        owner=primitive.owner_model,
        work=primitive.model,
        policy=replace(primitive.policy, batch_size=2),
    )
    facts = RecoveryStepExample(
        make_pending=lambda: PrimitiveOwner.objects.create(value="pending").pk,
        pending=lambda identity: PrimitiveOwner.objects.filter(pk=identity, value="pending").exists(),
        poison=None
        if seam is None
        else lambda identities: poison_recovery_call(implementation, seam, lambda self, identity: identity, identities),
    )
    examples = EffectExamples[PrimitiveWork](
        make_work=no_fixture,
        delivery=no_fixture,
        recovery_steps={RecoveryPhase.DISCOVERY: RecoveryStepBinding(run=capability.cleanup, example=lambda: facts)},
    )
    if implementation is PagedCleanup:
        # The normal author path supplies product facts without repeating run.
        examples = replace(examples, recovery_steps={RecoveryPhase.DISCOVERY: lambda: facts})
    with _installed(capability, "cleanup-control"):
        yield capability, examples


@contextmanager
def reap_override_binding(implementation):
    """A real registered adopter whose only custom step is its own ``_reap`` override."""
    capability = implementation.for_owner(owner=primitive.owner_model, work=primitive.model, policy=primitive.policy)

    def make_work():
        with capability.atomic(PrimitiveOwner.objects.create().pk, notify_worker=False) as pending:
            return pending.request_work(PrimitiveWork(payload="after", universe="acme"))

    with _installed(capability, "reap-override-control"):
        yield capability, EffectExamples[PrimitiveWork](make_work=make_work, delivery=no_fixture)


class TwoPagedCleanups(Cleanup):
    """Independent product predicates with the same metrics phase."""

    def derivation_steps(self):
        return tuple(
            RecoveryStep.paged(
                RecoveryPhase.DISCOVERY,
                lambda value=value: (
                    PrimitiveOwner.objects.filter(value=value).order_by("pk").values_list("pk", flat=True)
                ),
                self.process,
                size=2,
                key=value,
            )
            for value in ("orphans", "expired")
        )


@contextmanager
def two_cleanup_bindings():
    capability = TwoPagedCleanups.for_owner(
        owner=PrimitiveOwner,
        work=PrimitiveWork,
        policy=replace(primitive.policy, batch_size=2),
    )

    def example(value):
        return RecoveryStepExample(
            make_pending=lambda: PrimitiveOwner.objects.create(value=value).pk,
            pending=lambda identity: PrimitiveOwner.objects.filter(pk=identity, value=value).exists(),
        )

    examples = EffectExamples(
        make_work=no_fixture,
        delivery=no_fixture,
        recovery_steps={value: lambda value=value: example(value) for value in ("orphans", "expired")},
    )
    with _installed(capability, "two-cleanups"):
        yield capability, examples
