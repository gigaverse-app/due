"""Best-effort exception reporting without hiding worker shutdown requests."""

import logging

import sentry_sdk
from celery.exceptions import SoftTimeLimitExceeded

logger = logging.getLogger(__name__)


def capture_exception_safely(error: Exception) -> None:
    try:
        sentry_sdk.capture_exception(error)
    except SoftTimeLimitExceeded:
        raise
    except Exception:
        logger.exception("Due exception reporting failed")
