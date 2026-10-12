# Native lifecycle composition

Use these helpers when a product row already owns the work's states and identity.
They share Due's recovery isolation and database fencing without introducing a
second work row. The application still owns its indexed owed-work predicate,
worker bounds, retry budget and provider replay safety.

```text
product commit -> RecoveryRoute.request_progress(id) -> after-commit reservation
                                ^                            |
periodic recovery -> route.recover(batch_size=N)             v
                                                    (id, queue token)
                                                            |
                                  route.attempt_delivery -> execution token
                                                            |
                                  product/provider work -> fenced completion
```

## Choose a boundary

| Interface | What it owns | What the adopter supplies |
| --- | --- | --- |
| `native.recovery.dispatch_recovery_page` | Bounded page, whole-backlog observation, per-row failure isolation | Unsliced indexed query, dispatch callback, batch size |
| `native.recovery.RecoveryLease` | Queue reservation, token consumption, stale delivery rejection | Native nullable timestamp field and execution duration |
| `native.recovery.RecoveryRoute` | One binding shared by producer, sweep and worker | Lease, owed query, Celery task, reservation slack, maximum delay |
| `native.completion.CompletionLease` | Fenced DB-only finalization and completion receipt | Native claim/completion timestamps and product validator |
| `native.counters.saturating_increment` | A retry counter that cannot overflow PostgreSQL's integer ceiling | Existing counter value |

Import helpers from those modules; coordination internals are not extension points.
The [executable native tests](../tests/native) demonstrate concurrency, duplicate
messages, reservation expiry, lost publication, cancellation and a real worker.

## Recovery and completion are separate

A recovery reservation prevents an already queued row from being selected again.
The worker atomically replaces the queue token before executing. A duplicate or
superseded message cannot enter. `RecoveryRoute` validates that the worker's time
limits fit the lease; it rejects automatic Celery retry because replaying a consumed
token cannot reacquire the work. The sweep owns redelivery after expiry.

A completion lease protects product publication. Its `complete(..., finalize=...)`
callback runs under the native row lock and may commit database writes together
with the completion timestamp. It must not perform provider I/O. `release` cannot
clear a successor's claim. `held(retain_on_failure=True)` retains a failed claim
until its original expiry, including cancellation, so an ambiguous provider call
is treated like a crashed worker.

Example using a product's existing timestamp fields:

```python
from datetime import timedelta
from due.django.native.completion import CompletionLease

completion = CompletionLease(
    model=Delivery,
    claimed_at="completion_claimed_at",
    completed_at="completed_at",
    validate=validate_delivery,
)

with completion.held(delivery_id, lease_duration=timedelta(minutes=2), retain_on_failure=True) as token:
    if token is not None:
        # The provider operation must be independently safe to repeat.
        deliver_idempotently(delivery_id)
        completion.complete(delivery_id, token=token, finalize=publish_product_result)
```

`Delivery`, `validate_delivery`, the provider call and finalizer belong to the
application. A lease fences database authority; it cannot retract a request already
accepted by an external provider. Declare and test that provider contract with
pytest-obligation's native lifecycle bindings. See the
[architecture](architecture.md) and [integration guide](integration.md).
