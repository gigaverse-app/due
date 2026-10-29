# Which API should I use?

**For a background function, start with Django's `@task` and `.enqueue()` using
Due as its backend.** The [first tutorial](quickstart.md) shows how to submit a
job, retrieve its result and recover it after killing its worker.

Use `ReplaySafeEffect` plus `capability.atomic()` when work belongs to an editable
product and completion must publish only the current requested result. For
remote-state repair or uncertain provider outcomes, choose the capabilities
below. All use the same PostgreSQL/Celery recovery runtime.

For existing bulk-operation adopters, the [bounded settlement prototype](bounded-settlement.md)
describes an explicit multi-row API and its visibility and hook-replay tradeoffs.
It is experimental; ordinary single-row execution remains the default.

Most Due imports come from two entry points:

```python
# Application models, capabilities and startup:
from due.django.public import (
    DurableWorkPolicy,
    DurableWorkRegistration,
    Projection,
    ReplaySafeEffect,
    configure_durable_work,
    django_tasks_registration,
)

# Application tests (install django-due[testing]):
from due.django.conformance import EffectExamples, effect_contract_suite, hold_nudges
```

Import ORM-backed types from your Django models or after Django setup; call startup
helpers in `AppConfig.ready()`. The expanded entry points are available in the
source checkout, following 0.10.0. Existing module imports remain supported and
refer to the same classes/functions.

## A function that can safely run again

Your day-to-day API is Django's `@task`, `.enqueue()`, and `.get_result()`:

```python
from django.tasks import task


@task
def word_count(text: str) -> int:
    return len(text.split())


# In application code, after configuring Due as Django's task backend:
result = word_count.enqueue("Hello from Due")
# In a later request, after completion:
result = word_count.get_result(result.id)
```

Define the task in an importable module and list its path in `replay_safe_tasks`.
The [task tutorial](quickstart.md) supplies a prepared project and walks through
execution, result inspection and worker recovery. The
[setup guide](django-tasks-quickstart.md) supplies every configuration file for
your own project.
The [backend reference](django-tasks.md) covers delayed execution, queues and policy.

Use this for deterministic calculations or effects with established replay
safety. Each enqueue is a distinct invocation. A task might execute again after
a crash; Due guards its result row, while your task body must make its own writes
safe. Enqueue in the same database transaction as the product change it serves.

## A product outcome that needs guarded completion

The native API has three parts:

| Call or hook | Your application supplies | Due supplies |
| --- | --- | --- |
| `capability.atomic(owner_id)` and `pending.request_work(...)` | Product changes and captured inputs | Owner lock, atomic admission, revision change and post-commit notification |
| `perform_effect(work)` | Replay-safe calculation or external operation | Claimed execution outside the settlement transaction |
| `finalize_success(work, result)` | Local transactional product writes | Ownership/revision checks and locks before publication |

Bind your capability once, after its models are loaded:

```python
from examples.numbers.effects import DoubleNumber
from examples.numbers.models import CalculationWork

calculate = DoubleNumber.for_owner(work=CalculationWork)
```

The [native work lesson](native-work-tutorial.md) shows the full model, both hooks, the
producer command, and four failure histories. The
[document application](../../examples/documents/README.md) uses the same pattern
to publish immutable file revisions. A local fence cannot stop an old external
request; your effect must tolerate repetition or use a different capability.

Due infers the owner from the work model's declared one-to-one relation and uses
`DurableWorkPolicy()` unless you provide `policy=`. Explicit `owner=` is checked
against that relation. The standard policy has a five-minute lease, one-minute
retry delay, three rearms and seven-day retention. Provider operations that
require retained history still need `DurableWorkPolicy(retention=None)`.

Build the [application contract](conformance.md) alongside your hooks. The
arithmetic example includes real producer/provider bindings, generated failure
checks, independent enrollment and executed-report validation.

## Register the app once

Register native capabilities from `AppConfig.ready()`:

```python
from due.django.public import DurableWorkRegistration, configure_durable_work
from examples.numbers.effects import calculate

configure_durable_work([DurableWorkRegistration("calculate_number", calculate)], celery_app=app)
```

Here `app` is your configured Celery app. For Django Tasks with standard routing:

```python
from due.django.public import configure_durable_work

configure_durable_work(django_tasks=True, celery_app=app)
```

The task shortcut installs task imports and a recovery
schedule on the `default` queue every 30 seconds. For native capabilities, pass
their `DurableWorkRegistration` objects as the first argument; `django_tasks=True`
can include ordinary tasks in the same manifest. Use an explicit
`django_tasks_registration(...)` instead of the flag when you need a different
backend alias, queue, cadence or worker limits. Both forms use the same checks.

## Choose a richer capability when the requirement needs it

| Your requirement | API | Learn by example |
| --- | --- | --- |
| Repeatable background function and stored return value | `django.tasks.task` with `DueBackend` | [Run and recover a task](quickstart.md) |
| Repeatable effect with guarded local publication | `ReplaySafeEffect` | [Native work and completion hooks](native-work-tutorial.md) |
| Repair an external view after an old write lands late | `Projection` | [Chapter 3: projections](projection-tutorial.md) |
| Move or cancel a deadline | `ScheduledAction` | [Chapter 4: scheduling](scheduling-tutorial.md) |
| Preserve an uncertain, non-repeatable occurrence | `DurableExternalOperation` | [Chapter 5: uncertain outcomes](external-operation-tutorial.md) |
| Ask a provider whether an uncertain occurrence happened | `ObservedExternalOperation` | [Chapter 6: observation](observation-tutorial.md) |
| Recover cleanup independently of the original worker | `IndependentCompensation` | [Chapter 7: cleanup](compensation-tutorial.md) |
| Coordinate several calls with an execution context | `OwnedDurableWorkCapability` | [Chapter 7: protect the provider call](compensation-tutorial.md#3-protect-the-call-that-creates-output) |
| Hand off stages, complete inline and test failure histories | Completion and conformance helpers | [Chapter 8: composition](composition-tutorial.md) |

A provider timeout does not establish failure. For a non-repeatable operation,
retain uncertainty and seek correlated evidence before permitting another attempt.
See the [capability reference](capabilities.md) for the complete contracts.

## Imports you actually need

Use `due.django.public` for production authoring and startup, and
`due.django.conformance` for the common testing APIs. Implementation modules remain
available when you need a more specialized interface.

| Purpose | Import from | Names |
| --- | --- | --- |
| Ordinary task authoring | `django.tasks` | `task`, `TaskResultStatus` |
| Django Tasks registration | `due.django.public` | `django_tasks_registration` |
| Native work models, declarations and repeatable effects | `due.django.public` | `UniverseDurableWork`, `durable_work_schema`, `DurableWorkPolicy`, `ReplaySafeEffect`, `Capture` |
| Projection | `due.django.public` | `Projection` |
| Scheduling | `due.django.public` | `ScheduledAction`, `ScheduledWork` |
| Uncertain operations | `due.django.public` | `DurableExternalOperation`, `ExternalOperationWork` |
| Provider observation | `due.django.public` | `ObservedExternalOperation`, `ObservedExternalOperationWork`, `observation_index` |
| Independent cleanup | `due.django.public` | `IndependentCompensation` |
| Receipt storage | `due.django.public` | `JsonAppliedEffectEvidenceStore`, `StoredAppliedEffectEvidence` |
| Startup and routing | `due.django.public` | `DurableWorkRegistration`, `configure_durable_work`, `ApplicationHooks`, `configure_application` |
| Native application contracts | `due.django.conformance` | `effect_contract_suite`, `EffectExamples`, `work_table_host`, `ProducerExample`, `AdmissionFailureExample`, `SupersessionExample`, `TerminalExample` |
| Contract inventory | `due.django.conformance` | `discover_enrollment`, `assert_complete_enrollment` |
| Provider bindings and controlled delivery | `due.django.conformance` | `ProviderSeam`, `seam_examples`, `ProviderProbe`, `WorkTableDelivery`, `WorkTableRecovery` |
| Application fault controls | `due.django.conformance` | `hold_nudges`, `expire_lease`, `die_after`, `WorkerDied`, `applied_snapshot` |
| Real Celery worker failures | `due.django.conformance` | `celery_worker_contract` |

`due.django.public` also exports lower-level coordination functions such as
`claim_work` and `complete_work`. Most integrations should use a capability's
admission and execution methods. Start with the supported capability that owns
the behavior before composing runtime primitives yourself.

## Where should my code go?

Use your own Django app structure. Due does not require a repository layout.
This convention keeps the small task and native paths easy to navigate:

| File in your app | Put here |
| --- | --- |
| `models.py` | Product models; native work models when needed |
| `tasks.py` | Ordinary Django task functions |
| `effects.py` | Native capability classes and their configured instances |
| `services.py` | Product commands that transactionally admit work |
| `apps.py` | One `ready()` registration manifest, shared by every process |
| `tests/` | Replay/transaction tests; native contracts, enrollment and report checks |

For exact startup settings use the [task walkthrough](django-tasks-quickstart.md) or
[native integration reference](integration.md). For richer application bindings
use the [conformance walkthrough](conformance.md).

## Where is it in the Due repository?

| Directory | What it owns |
| --- | --- |
| `package/src/due/django/` | Installable Django runtime and the APIs above |
| `package/docs/` | Tutorials and API/operations guides that travel with the library |
| `docs/` | Longer application stories, comparisons and project records |
| `examples/` | Runnable application code; [catalog](../../examples/README.md) |
| `integration_tests/` | Tests of those applications with real dependencies |
| `package/tests/` | Library tests independent of the repository's example apps |
| `documentation/` | Documentation homepage, assets and publishing machinery |

The website publishes the canonical guides directly from their owning locations.
Read through the website navigation or the links above; there is no second copy
of the API guide to keep in sync. Maintainers can consult
[documentation ownership](../../docs/package-documentation-map.md) for the subtree
layout and [package development](development.md) for source changes.
