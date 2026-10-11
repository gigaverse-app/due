"""Default soft and hard execution bounds for registered Celery workers.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

EXECUTION_TIME_LIMIT_SECONDS = 60
EXECUTION_SOFT_TIME_LIMIT_SECONDS = 50
