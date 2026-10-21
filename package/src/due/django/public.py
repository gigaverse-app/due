"""The durable-work library's public authoring imports and low-level coordination exports.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

from due.errors import CompletionStatus, EffectNotAppliedError, EffectRejection, WorkIncomplete
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
from .capabilities.projection import projections_with_stale_writes
from .declarations.capture import Capture
from .declarations.dependents import Dependent
from .declarations.eligibility import ExecutionEligibility
from .declarations.external_services import ExternalService
from .declarations.mirrors import Outcome, StatusMirror
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
from .runtime.transport import run_effect_coroutine

__all__ = [
    "AppliedEffectEvidence",
    "EffectNotAppliedError",
    "EffectRejection",
    "ExponentialDelay",
    "ExecutionEligibility",
    "Capture",
    "Outcome",
    "StatusMirror",
    "ExternalService",
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
