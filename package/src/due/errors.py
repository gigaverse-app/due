"""Provider evidence and completion errors usable without importing the ORM.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

from datetime import timedelta
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    #: The provider's minimum wait before this work may run again, such as an
    #: HTTP ``Retry-After``. The retry is due no earlier than this, however short
    #: the policy's delay. Omitted from the persisted decision when unset, so
    #: evidence written without it reads unchanged on every version.
    retry_after: timedelta | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def _retry_after_needs_a_retry(self) -> Self:
        if self.retry_after is not None:
            if self.retry_after < timedelta(0):
                raise ValueError("retry_after must be nonnegative")
            if self.disposition is not WorkFailureDisposition.RETRYABLE:
                raise ValueError("retry_after describes a retry; a terminal rejection never runs again")
        return self


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
