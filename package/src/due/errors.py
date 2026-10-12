"""Provider evidence and completion errors usable without importing the ORM.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class WorkFailureDisposition(StrEnum):
    """The domain's bounded classification of a definite handler failure."""

    RETRYABLE = "retryable"
    TERMINAL = "terminal"
    UNKNOWN = "unknown"


class EffectRejection(BaseModel):
    """Versioned, redacted application decision accompanying definite nonapplication."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    message: str
    disposition: Literal[WorkFailureDisposition.RETRYABLE, WorkFailureDisposition.TERMINAL] = (
        WorkFailureDisposition.RETRYABLE
    )


class EffectNotAppliedError(Exception):
    """
    The adapter proves the logical effect did not occur in this attempt.

    Use only before mutation entry or for a provider's definite rejection.
    A timeout, disconnect or generic 5xx after mutation entry does not qualify.
    Preparatory uploads can exist without publishing a post; their cleanup is
    a separate obligation and must not be confused with publication evidence.
    """

    def __init__(self, rejection: str | EffectRejection) -> None:
        # Existing string-only callers retain retryable nonapplication semantics.
        self.rejection = EffectRejection(message=rejection) if isinstance(rejection, str) else rejection
        super().__init__(self.rejection.message)


class EffectOutcomeUnknownError(Exception):
    """A possible mutation has no definite result; another attempt is forbidden."""


class CompletionStatus(StrEnum):
    """Where one admitted revision stands after a synchronous caller's attempt."""

    COMPLETED = "completed"
    # A newer revision replaced it before completion; that revision is owed.
    SUPERSEDED = "superseded"
    # Another execution holds its reservation (a delivered nudge or recovery).
    IN_PROGRESS = "in_progress"
    # Committed and not finished now: backoff, continuation, repair or UNKNOWN.
    OWED = "owed"
    # The revision stopped in a terminal or exhausted failure.
    STOPPED = "stopped"
    # The caller's enclosing transaction has not committed the admission yet.
    DEFERRED = "deferred"
    # The producer admitted nothing (e.g. a legacy-owned owner).
    NOT_ADMITTED = "not_admitted"
    # The row no longer exists, so its outcome cannot be read.
    MISSING = "missing"


class WorkIncomplete(RuntimeError):
    """The requested revision did not complete; its existing lifecycle still owns recovery."""

    def __init__(self, message: str, *, status: CompletionStatus) -> None:
        super().__init__(message)
        self.status = status
