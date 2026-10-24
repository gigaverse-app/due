"""Give the story's ten-minute render room to finish and settle."""

from datetime import timedelta

from due.django.public import DurableWorkPolicy

RENDER_SOFT_LIMIT = 11 * 60
RENDER_HARD_LIMIT = 12 * 60
RENDER_POLICY = DurableWorkPolicy(retention=None, lease_duration=timedelta(minutes=15))
