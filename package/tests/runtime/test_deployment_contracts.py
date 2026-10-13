"""An adopter can keep existing queue, metric and evidence identities at startup."""

import json
import subprocess
import sys

import pytest


@pytest.mark.no_db
@pytest.mark.parametrize("custom", [False, True], ids=["defaults", "existing-application"])
def test_startup_preserves_configured_deployment_contracts(custom: bool) -> None:
    # A fresh interpreter matters: Django models, Celery tasks and Prometheus
    # collectors bind their names once, before an application's first delivery.
    configured = (
        {
            "DUE_TASK_NAMESPACE": "legacy.work.tasks",
            "DUE_METRIC_NAMESPACE": "legacy_work",
            "DUE_EFFECT_ATTEMPT_TABLE": "legacy_attempt",
            "DUE_RECOVERY_DEFERRAL_TABLE": "legacy_deferral",
        }
        if custom
        else {}
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json, sys
import django
from django.conf import settings
settings.configure(INSTALLED_APPS=['due.django'], SECRET_KEY='test', **json.loads(sys.argv[1]))
django.setup()
from celery import current_app
from prometheus_client import REGISTRY
from due.django.models import EffectAttempt, RecoveryDeferral
from due.django.runtime.metrics import DurableWorkOperation, metric_schemas, record_work_operation
from due.django.runtime.names import METRIC_NAMESPACE, RUN_TASK, RECOVERY_TASK, OBSERVE_TASK
from due.django.runtime.tasks import run_durable_work_task, recover_durable_work_task, observe_durable_work_task

tasks = [run_durable_work_task, recover_durable_work_task, observe_durable_work_task]
names = [RUN_TASK, RECOVERY_TASK, OBSERVE_TASK]
assert [task.name for task in tasks] == names
assert all(current_app.tasks[name] is task._get_current_object() for name, task in zip(names, tasks))
schemas = metric_schemas()
operations = f'{METRIC_NAMESPACE}_operations_total'
assert schemas[operations]['operation'] is DurableWorkOperation
for immutable in (schemas, schemas[operations]):
    try:
        immutable['invented'] = str
    except TypeError:
        pass
    else:
        raise AssertionError('metric catalogs must be read-only')
record_work_operation('example.work', DurableWorkOperation.CLAIMED)
assert REGISTRY.get_sample_value(operations, {'effect': 'example.work', 'operation': 'claimed'}) == 1
print(json.dumps({'tasks': names, 'metrics': sorted(schemas),
                  'tables': [EffectAttempt._meta.db_table, RecoveryDeferral._meta.db_table]}))
""",
            json.dumps(configured),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    actual = json.loads(result.stdout)
    task_prefix = configured.get("DUE_TASK_NAMESPACE", "due.django.runtime.tasks")
    metric_prefix = configured.get("DUE_METRIC_NAMESPACE", "due")
    assert actual["tasks"] == [f"{task_prefix}.{name}_durable_work_task" for name in ("run", "recover", "observe")]
    assert len(actual["metrics"]) == 16
    assert all(name.startswith(f"{metric_prefix}_") for name in actual["metrics"])
    assert actual["tables"] == [
        configured.get("DUE_EFFECT_ATTEMPT_TABLE", "due_effectattempt"),
        configured.get("DUE_RECOVERY_DEFERRAL_TABLE", "due_recoverydeferral"),
    ]
