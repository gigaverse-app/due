# Which API should I use?

**Start with Django Tasks for a repeatable background function.** Use Due's
native capabilities when you need to coordinate a product's current revision,
repair remote state, or resolve an uncertain provider outcome. Both use the same
PostgreSQL/Celery recovery runtime and can live in the same application.

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
The [trivial greeting tutorial](quickstart.md) supplies the complete configuration,
worker, scheduler, result inspection and application tests.
The [backend reference](django-tasks.md) covers delayed execution, queues and policy.

Use this for deterministic calculations or effects with established replay
safety. Each enqueue is a distinct invocation. A task might execute again after
a crash; Due guards its result row, while your task body must make its own writes
safe. Enqueue in the same database transaction as the product change it serves.

## A product outcome that needs guarded completion

**`ReplaySafeEffect.for_owner(...)` and `capability.atomic(...)` are the main
native authoring APIs for repeatable work.** They connect an application model,
captured inputs, an external operation and its local completion.

For example, publishing a document has two steps: write immutable revision bytes,
then update the document's published pointer. An old worker may finish late;
only the current owner and requested revision should publish that pointer.

The existing [document application](../../examples/documents/README.md) implements
that split. Its capability has these two central methods (excerpt; full imports,
failure callbacks and registration are in the linked application):

```python
from due.django.public import ReplaySafeEffect
from examples.documents import storage
from examples.documents.models import Document, PublicationWork


class PublishDocument(ReplaySafeEffect[Document, PublicationWork, str]):
    def perform_effect(self, work: PublicationWork) -> str:
        return storage.write_revision(work.text, work.provider_idempotency_key())

    def finalize_success(self, work: PublicationWork, result: str) -> None:
        Document.objects.filter(pk=work.owner_id).update(published_key=result, publication_failed=False)
```

`perform_effect()` runs external I/O outside the settlement transaction.
`finalize_success()` runs under Due's ownership and revision checks, with the
owner/work locks held. Keep that callback to local transactional writes. The
storage adapter must honor the immutable key; a local fence cannot stop an old
external request from reaching a provider.

Application commands use the registered instance, rather than constructing a
capability for each request. In the runnable document application:

```python
from examples.documents.effects import publish_document
from examples.documents.models import PublicationWork


def publish(document_id: int, text: str):
    with publish_document.atomic(document_id) as pending:
        document = pending.locked_owner
        document.text = text
        document.publication_failed = False
        document.save(update_fields=["text", "publication_failed"])
        return pending.request_work(PublicationWork(text=text, universe="example"))
```

The application wires that instance once:

```python
publish_document = PublishDocument.for_owner(work=PublicationWork)
```

Due infers the owner from the work model's declared one-to-one relation and uses
`DurableWorkPolicy()` unless you provide `policy=`. You can still pass `owner=`
explicitly; Due checks that it matches the relation. The standard policy has a
five-minute lease, one-minute retry delay, three rearms and seven-day retention.
Provider operations that require retained history still need an explicit
`DurableWorkPolicy(retention=None)`; the shortcut does not relax their validation.

The `atomic()` block locks the owner and commits product intent with its work;
`request_work()` captures the desired inputs and arranges a post-commit notification.
`universe` is the tenant key; a single-tenant application can use `""`.

Follow [Printworks, stage 2](../../docs/acme-printworks-tutorial.md#2-customers-want-proofs-and-an-old-worker-finishes-late)
for the step-by-step move from a task to a native capability. Build the
[application contract](conformance.md) alongside it. A working callback alone
does not establish replay safety, stale-result handling or recovery.

## Register the app once

For Django Tasks with standard routing, call this from `AppConfig.ready()`:

```python
from due.django.public import configure_durable_work

configure_durable_work(django_tasks=True, celery_app=app)
```

Here `app` is your configured Celery app. This installs task imports and a recovery
schedule on the `default` queue every 30 seconds. For native capabilities, pass
their `DurableWorkRegistration` objects as the first argument; `django_tasks=True`
can include ordinary tasks in the same manifest. Use an explicit
`django_tasks_registration(...)` instead of the flag when you need a different
backend alias, queue, cadence or worker limits. Both forms use the same checks.

## Choose a richer capability when the requirement needs it

| Your requirement | API | Learn by example |
| --- | --- | --- |
| Repeatable background function and stored return value | `django.tasks.task` with `DueBackend` | [First task](quickstart.md) |
| Repeatable effect with guarded local publication | `ReplaySafeEffect` | [Proof publication](../../docs/acme-printworks-tutorial.md#2-customers-want-proofs-and-an-old-worker-finishes-late) |
| Repair an external view after an old write lands late | `Projection` | [Approval portal](../../docs/acme-printworks-tutorial.md#3-the-approval-portal-can-be-overwritten-remotely) |
| Move or cancel a deadline | `ScheduledAction` | [Scheduled publication](../../examples/scheduled/README.md) |
| Preserve an uncertain, non-repeatable occurrence | `DurableExternalOperation` | [Evidence and replay rules](capabilities.md#uncertainty-and-evidence) |
| Ask a provider whether an uncertain occurrence happened | `ObservedExternalOperation` | [Print orders](../../examples/orders/README.md) |
| Recover cleanup independently of the original worker | `IndependentCompensation` | [Printworks](../../docs/acme-printworks-tutorial.md) |

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
| Native application contracts | `due.django.conformance` | `effect_contract_suite`, `EffectExamples`, `work_table_host` |
| Provider bindings and controlled delivery | `due.django.conformance` | `ProviderSeam`, `seam_examples`, `ProviderProbe`, `WorkTableDelivery`, `WorkTableRecovery` |
| Application fault controls | `due.django.conformance` | `hold_nudges`, `expire_lease`, `die_after` |

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

For exact startup settings use the [task tutorial](quickstart.md) or
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
