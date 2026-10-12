# Completion on an existing lifecycle

`due.django.completion.CompletionLease` fences completion on a model that already
owns its state. Configure two nullable `DateTimeField` columns and a validator
that accepts only product states allowed to complete. An optional `updated_at`
column is refreshed on writes. This does not enroll a work table or schedule
recovery: the application's existing sweep still owns delivery.

```text
claim under row lock -> provider call -> complete(token, finalize)
         |                                  |
         +-- expired -> successor claim     +-- product writes + receipt commit
                              |
                  old token cannot complete or release
```

```python
from due.django.completion import CompletionLease

completion = CompletionLease(
    model=Publication,
    claimed_at="completion_claimed_at",
    completed_at="published_at",
    validate=require_publishable,
    updated_at="updated_at",
)

with completion.held(publication_id, retain_on_failure=True) as token:
    if token is not None:
        publish_to_provider(publication_id)  # Must tolerate replay after rollback.
        completion.complete(publication_id, token=token, finalize=mark_visible)
```

The application supplies `Publication`, `require_publishable`,
`publish_to_provider`, and `mark_visible`. The finalizer receives the locked row
and performs database writes only. Its failure rolls back both its changes and
the receipt. A stale token returns `False` without entering the finalizer.
Changing ownership columns inside the finalizer raises `WorkOwnershipLost`.

`held()` releases on exit by default. `retain_on_failure=True` keeps the original
claim after an exception or cancellation until its original expiry; it never
extends the deadline. Once completed, later scope cleanup cannot erase the
receipt. `available(queryset, lease_duration=...)` filters an unsliced queryset
for the configured model, preserving tenant/product filters. Selection is only
a hint: `claim()` checks ownership again under the row lock.

A lease cannot stop an old provider call. Provider idempotency or replay safety
remains an application requirement. The [database regression tests](../tests/test_completion_lease.py)
and [failure/cancellation histories](../tests/test_completion_lease_histories.py)
exercise successor fencing, retained expiry, rollback, scoped selection and
real asynchronous task cancellation through the sync bridge.
