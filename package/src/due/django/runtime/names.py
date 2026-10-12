"""Stable transport and telemetry names, configurable before Django starts.

Keep an existing namespace during a rolling migration so old workers can consume
new publications and dashboards continue reading the same series.
"""

from django.conf import settings

TASK_NAMESPACE: str = getattr(settings, "DUE_TASK_NAMESPACE", "due.django.runtime.tasks")
METRIC_NAMESPACE: str = getattr(settings, "DUE_METRIC_NAMESPACE", "due")
RUN_TASK = f"{TASK_NAMESPACE}.run_durable_work_task"
RECOVERY_TASK = f"{TASK_NAMESPACE}.recover_durable_work_task"
OBSERVE_TASK = f"{TASK_NAMESPACE}.observe_durable_work_task"
