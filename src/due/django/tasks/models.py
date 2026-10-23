"""One immutable invocation per enqueue; coordination belongs to Due."""

import uuid

from django.db import models

from due.django.models import UniverseDurableWork, durable_work_schema


class TaskWork(UniverseDurableWork):
    result_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    backend = models.CharField(max_length=128)
    task_path = models.CharField(max_length=512)
    queue_name = models.CharField(max_length=128)
    args = models.JSONField(default=list)
    kwargs = models.JSONField(default=dict)
    takes_context = models.BooleanField(default=False)
    run_after = models.DateTimeField(null=True)
    started_at = models.DateTimeField(null=True)
    last_attempted_at = models.DateTimeField(null=True)
    worker_ids = models.JSONField(default=list)
    errors = models.JSONField(default=list)
    return_value = models.JSONField(null=True)

    class Meta:
        indexes, constraints = durable_work_schema("due_task")
