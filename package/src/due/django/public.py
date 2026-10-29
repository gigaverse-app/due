"""Application authoring and startup imports, plus existing coordination exports.

Use this facade for models, capabilities, evidence and registration. Import after
Django has loaded applications (models.py or AppConfig.ready, as appropriate).
Testing integrations live in due.django.conformance and remain optional.
"""

from due.errors import (
    CompletionStatus,
    EffectNotAppliedError,
    EffectOutcomeUnknownError,
    EffectRejection,
    WorkDeferredError,
    WorkIncomplete,
)
from due.observations import AppliedEffectEvidence
from due.retry_delays import ExponentialDelay

from .capabilities.authoring import (
    AppliedEvidenceContract,
    Completion,
    CompletionErrors,
    DatabaseEffect,
    Discovery,
    DiscoveryHorizon,
    DurableWorkCapability,
    DurableWorkPolicy,
    OwnedDurableWorkCapability,
    ReplayContract,
    ReplaySafeEffect,
    WorkOwnerBusy,
    registered_nudge,
)
from .capabilities.compensation import IndependentCompensation, ProtectedOutput
from .capabilities.observed_operations import (
    ObservedExternalOperation,
    ObservedExternalOperationWork,
    observation_index,
)
from .capabilities.operations import DurableExternalOperation, ExternalOperationWork
from .capabilities.projection import DurableProjection, Projection, projections_with_stale_writes
from .capabilities.scheduled import (
    ActionOutcome,
    CancellationResult,
    DurableScheduledAction,
    ScheduledAction,
    ScheduledWork,
    ScheduleResult,
)
from .declarations.capture import Capture
from .declarations.dependents import Dependent
from .declarations.eligibility import ExecutionEligibility
from .declarations.execution_resources import ExecutionResource
from .declarations.external_services import ExternalService
from .declarations.mirrors import Outcome, StatusMirror
from .evidence.effect_evidence import (
    AppliedEffectEvidenceStore,
    JsonAppliedEffectEvidenceStore,
    StoredAppliedEffectEvidence,
)
from .identities import WorkId
from .integration import ApplicationHooks
from .integration import configure as configure_application
from .models import (
    DurableWork,
    DurableWorkState,
    UniverseDurableWork,
    durable_work_schema,
)
from .runtime.coordination import (
    ContinueAfter,
    ReopenAfter,
    WakeAction,
    WorkClaim,
    WorkExecutionContext,
    WorkFailureDisposition,
    WorkOwnershipLost,
    claim_work,
    complete_work,
    database_now,
    dispatch_due_work,
    due_work,
    outstanding_work,
    prune_completed_work,
    publish_work_census,
    reap_expired_work,
    renew_work,
    require_fenced_settlement,
    retry_work,
    run_work,
    supersede_work,
    terminal_work,
)
from .runtime.registry import DurableWorkRegistration, configure_durable_work, django_tasks_registration
from .runtime.transport import run_effect_coroutine

__all__ = [
    "WorkId",
    "ActionOutcome",
    "ApplicationHooks",
    "AppliedEffectEvidenceStore",
    "CancellationResult",
    "DurableExternalOperation",
    "DurableProjection",
    "DurableScheduledAction",
    "DurableWorkRegistration",
    "EffectOutcomeUnknownError",
    "ExternalOperationWork",
    "IndependentCompensation",
    "JsonAppliedEffectEvidenceStore",
    "ObservedExternalOperation",
    "ObservedExternalOperationWork",
    "Projection",
    "ProtectedOutput",
    "ScheduledAction",
    "ScheduledWork",
    "ScheduleResult",
    "StoredAppliedEffectEvidence",
    "configure_application",
    "configure_durable_work",
    "django_tasks_registration",
    "observation_index",
    "AppliedEffectEvidence",
    "EffectNotAppliedError",
    "EffectRejection",
    "ExponentialDelay",
    "ExecutionEligibility",
    "Capture",
    "Outcome",
    "StatusMirror",
    "ExternalService",
    "ExecutionResource",
    "ContinueAfter",
    "ReopenAfter",
    "run_effect_coroutine",
    "AppliedEvidenceContract",
    "Completion",
    "CompletionErrors",
    "CompletionStatus",
    "DatabaseEffect",
    "DurableWork",
    "DurableWorkCapability",
    "DurableWorkPolicy",
    "WorkOwnerBusy",
    "WorkIncomplete",
    "WorkDeferredError",
    "DurableWorkState",
    "OwnedDurableWorkCapability",
    "Discovery",
    "DiscoveryHorizon",
    "ReplayContract",
    "ReplaySafeEffect",
    "UniverseDurableWork",
    "WorkFailureDisposition",
    "WorkClaim",
    "WorkExecutionContext",
    "WorkOwnershipLost",
    "WakeAction",
    "Dependent",
    "claim_work",
    "complete_work",
    "database_now",
    "durable_work_schema",
    "dispatch_due_work",
    "due_work",
    "outstanding_work",
    "publish_work_census",
    "prune_completed_work",
    "projections_with_stale_writes",
    "reap_expired_work",
    "renew_work",
    "require_fenced_settlement",
    "registered_nudge",
    "retry_work",
    "run_work",
    "supersede_work",
    "terminal_work",
]
