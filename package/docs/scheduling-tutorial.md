# 4. Schedule, move and cancel work

[Previous: projections](projection-tutorial.md) · [All chapters](tutorial.md)

You want to publish a text file at a particular time. Before publication starts,
the user can move that time or cancel it. `ScheduledAction` stores the deadline
with the work and checks it again when a delivery arrives.

Use the [prepared native shell](tutorial.md#prepare-the-native-chapters-once).
The operation here writes a short text file to local storage.

## 1. Use a scheduled work model

`ScheduledWork` is Due's abstract work-model base for deadlines. It extends the
`UniverseDurableWork` model introduced in chapter 2 with `run_at` (the requested
execution time) and `cancelled` (whether pending work was cancelled). Add our
captured application input, `text`:

```python
from typing import ClassVar

from django.db import models
from due.django.public import ScheduledWork, durable_work_schema
from examples.scheduled.models import Publication  # The example's application model


class PublicationDeadline(ScheduledWork):
    work_owner_field: ClassVar[str] = "owner"
    owner = models.OneToOneField(Publication, on_delete=models.SET_NULL, null=True)
    text = models.TextField()

    class Meta:
        indexes, constraints = durable_work_schema("publication_deadline")
```

The product `Publication` stores the text, requested time and displayed status.
Both models are supplied in [models.py](../../examples/scheduled/models.py).
`PublicationDeadline` is our model name. Its owner metadata and schema helper
follow [chapter 2's model declarations](native-work-tutorial.md#1-define-the-application-record-and-its-work-row).
`schedule_at()` will populate the inherited scheduling fields for us.

## 2. Define when the action is needed and what it does

`ScheduledAction` is Due's capability base for a replay-safe operation with a
deadline. `PublishAtDeadline` is our implementation. These are its Due hooks:

| Hook | Your application supplies |
| --- | --- |
| `is_still_needed(work)` | Whether the application still needs the action when execution is attempted |
| `perform_effect(work)` | The operation to run when Due permits it |
| `on_completed(work, outcome)` | Local completion writes; `ActionOutcome` is Due's result enum, and `PERFORMED` means the operation ran |

Our [storage adapter](../../examples/documents/storage.py) writes text to a file.
`write_revision(text, key)` accepts an equal repeat and rejects different text for
the same key. `revision_key(key)` derives its file name. Both functions are example
code. Due supplies `work.provider_idempotency_key()`: a stable key for one requested
revision, reused across attempts and changed for new input. That key works because
our storage adapter enforces the equal-repeat rule.

Here are the central hooks from [effects.py](../../examples/scheduled/effects.py):

```python
from due.django.public import ActionOutcome, DurableWorkPolicy, ScheduledAction
from examples.documents import storage
from examples.scheduled.models import Publication, PublicationDeadline


class PublishAtDeadline(ScheduledAction[Publication, PublicationDeadline]):
    def is_still_needed(self, work: PublicationDeadline) -> bool:
        return Publication.objects.filter(pk=work.owner_id, status=Publication.Status.SCHEDULED).exists()

    def perform_effect(self, work: PublicationDeadline) -> None:
        storage.write_revision(work.text, work.provider_idempotency_key())

    def on_completed(self, work: PublicationDeadline, outcome: ActionOutcome) -> None:
        if outcome == ActionOutcome.PERFORMED:
            Publication.objects.filter(pk=work.owner_id).update(
                status=Publication.Status.PUBLISHED,
                published_key=storage.revision_key(work.provider_idempotency_key()),
            )


publish_at_deadline = PublishAtDeadline.for_owner(
    work=PublicationDeadline,
    input_fields=("universe", "text", "run_at", "cancelled"),
    policy=DurableWorkPolicy(retention=None),
)
```

The file key belongs to the captured revision, so repeating the write is safe.
Due checks the deadline and ownership before running and settling the action.
The prepared class also enables short deadline wake-up hints and records an
exhausted attempt in the product status.

As in chapter 3, `input_fields` defines requested input and `DurableWorkPolicy`
sets runtime behavior. Include `run_at` and `cancelled` because moving or cancelling
the action changes that input. Retained work (`retention=None`) and attempt
evidence let Due recognize a call that may already have started.

## 3. Request a deadline

Due's `schedule_at(owner_id, desired_work, at=deadline)` records the deadline and
captured input, returning a `ScheduleResult` enum. Given an existing publication
ID and a timezone-aware deadline, the native call is:

```python
publish_at_deadline.schedule_at(
    publication_id,
    PublicationDeadline(text="Hello from Due", universe="example"),
    at=deadline,
)
```

The prepared application wraps that call with product updates in one transaction.
Try its command with a timezone-aware time:

```python
from datetime import timedelta
from django.utils import timezone
from due.django.conformance import hold_nudges
from examples.scheduled.services import create_publication

with hold_nudges():
    publication = create_publication("Hello from Due", timezone.now() + timedelta(minutes=1))

print(publication.status)  # scheduled
```

Here `hold_nudges()` withholds the immediate notification for the exercise.
PostgreSQL still records the deadline and owed work.

## 4. Move the deadline or cancel it

Use the same product ID:

```python
from examples.scheduled.services import cancel_publication, reschedule_publication

with hold_nudges():
    moved = reschedule_publication(publication.pk, timezone.now() + timedelta(minutes=2))
print(moved.value)  # scheduled

cancelled = cancel_publication(publication.pk)
print(cancelled.value)  # cancelled
```

These commands call `.schedule_at()` and `.cancel_pending()` under the product
lock. An old queued notification cannot restore the old deadline or undo the
cancellation. Once the provider call has started, the result can be
`already_started`; the application must show that refusal instead of claiming
the action was cancelled.

`create_publication`, `reschedule_publication` and `cancel_publication` are our
[application commands](../../examples/scheduled/services.py), wrapping those Due
methods with `Publication` updates. `.value` is the string value of Due's
`ScheduleResult` or `CancellationResult` enum, not a Django model field.

## 5. Recover overdue work

Create a fresh publication, then use the controlled recovery driver to advance
to its deadline without waiting a minute:

```python
from due.django.conformance import WorkTableDelivery
from examples.scheduled.effects import publish_at_deadline

with hold_nudges():
    due = create_publication("Time to publish", timezone.now() + timedelta(minutes=1))

with WorkTableDelivery(publish_at_deadline).session() as delivery:
    delivery.recover()

due.refresh_from_db()
publication.refresh_from_db()
print(due.status, bool(due.published_key))  # published True
print(publication.status)  # cancelled
```

The driver advances the test clock and invokes the registered runtime. In a
running service, Beat finds overdue work and workers perform it. Publication
may be late during an outage; it must not occur before its deadline.

Run the deadline, rescheduling and cancellation checks:

```bash
uv run pytest integration_tests/examples/test_scheduled.py integration_tests/conformance/test_scheduled_contract.py -v
```

[Next: preserve an uncertain outcome](external-operation-tutorial.md)
