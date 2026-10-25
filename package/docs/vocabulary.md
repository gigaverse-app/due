# Words for background work that breaks

Perhaps something in your app goes wrong only *sometimes*. An email never goes
out. A customer is charged twice. A thumbnail shows last week's picture after a
newer one was uploaded. A deploy restarts the workers and a few jobs are never
seen again. These failures are common, and they have names. This page gives you
those names, starting from the failure each one addresses.

Read it once before the tutorial, or come back when a chapter links a word here.
You do not need any of these words to finish [chapter 1](quickstart.md).

## The failures, and the chapter that shows each one

| What you see | What actually happened | Words to look up | Chapter |
| --- | --- | --- | --- |
| A job never ran, but nothing failed | The worker died, or the message telling it to start was lost | [owed work](#owed-work-and-the-work-row), [nudge](#nudge), [recovery](#recovery) | [1](quickstart.md), [2](native-work-tutorial.md) |
| A job ran although the request that created it failed | The job was queued before the database transaction rolled back | [admission](#admission) | [1](quickstart.md), [2](native-work-tutorial.md) |
| A job ran twice | A worker was presumed dead and another one started the same job | [lease](#attempt-claim-and-lease), [replay-safe](#replay-and-replay-safe) | [1](quickstart.md), [2](native-work-tutorial.md) |
| An old result replaced a newer one | A slow job for the old input finished after the job for the new input | [revision](#revision), [fencing token](#fencing-token), [settlement](#settlement) | [2](native-work-tutorial.md) |
| A search index or cache shows an old value | An old write reached the external system after a newer one | [projection and repair](#projection-and-repair) | [3](projection-tutorial.md) |
| Something happened before it was supposed to, or after it was cancelled | A queued message still carried the old time | [captured input](#captured-input) | [4](scheduling-tutorial.md) |
| A customer was charged twice after a timeout | The provider accepted the first request, but its reply was lost, so the app tried again | [unknown outcome](#unknown-outcome), [evidence](#evidence-and-receipt), [idempotency key](#idempotency-key) | [5](external-operation-tutorial.md), [6](observation-tutorial.md) |
| Temporary files pile up | The process died between creating the file and deleting it | [compensation](#compensation) | [7](compensation-tutorial.md) |

## The words

### Product row and owner

The row in your own Django model that a user cares about: a document, an order,
a book. Due calls it the **owner** of the work done for it. Your product row is
an ordinary Django model; Due never adds fields to it.

### Owed work and the work row

**Owed work** (also called an *obligation*) is the fact "this still needs to be
done", stored in PostgreSQL. A message in a queue is not enough, because a queue
message can be lost. Due stores owed work as a **work row** in a **work table**:
a Django model you define by inheriting a Due base class. The work row holds the
input the job needs plus the bookkeeping Due uses to run and recover it.

"Durable work" means work recorded this way: it survives crashes, restarts and
lost messages because the database, not a process or a queue, remembers it.

### Admission

Writing the work row. **Atomic admission** means the work row is written in the
*same database transaction* as the product change that made it necessary. If
that transaction rolls back, the work row disappears with it. If it commits,
both exist. You never get "saved the order, forgot to send the email" or "sent
the email for an order that was never saved".

### Nudge

After the transaction commits, Due sends Celery a short message: "work row 42 is
ready". This is a **nudge**. It makes the work start quickly, but it is only a
hint. If the nudge is lost (the broker restarts, the process exits right after
commit), the work row is still there and [recovery](#recovery) will find it.

### Recovery

A recurring job, run by Celery Beat, that scans the work tables for work that is
owed but not progressing: rows whose nudge was lost, and rows whose worker
stopped before finishing. It hands them to workers again. Recovery is why you
must keep Beat running even when everything looks healthy.

### Attempt, claim and lease

An **attempt** is one try at doing a job. When a worker starts an attempt, it
**claims** the work row: it marks the row as in progress and records a
**lease**, a deadline by which it must finish. A database cannot tell a dead
worker from a slow one. The lease answers that question by agreement: once it
expires, recovery may give the work to another worker.

The cost is that a worker which was only slow may still be running when a second
attempt starts. Due's [fencing token](#fencing-token) stops the slow one from
saving its result.

### Replay and replay-safe

A **replay** is running a job again from the beginning, because an earlier
attempt might not have finished. An operation is **replay-safe** when running it
twice does no harm. Computing a number, overwriting the same file at the same
path, or setting a field to a value are usually replay-safe. Sending an email,
charging a card or placing an order usually are not: each run creates another
one.

Due can recover replay-safe work automatically. For work that is not replay-safe
you need either an [idempotency key](#idempotency-key) the provider honours, or
[evidence](#evidence-and-receipt) of what already happened.

### Idempotency key

A name for "this particular request" that you send with it, so the provider can
recognise a repeat and return the first result instead of acting again. Many
payment APIs accept one. Each work row offers `provider_idempotency_key()`,
which stays the same across retries of the same input and changes when the
input changes.

### Captured input

The values a job needs, copied into the work row at the moment the work is
requested. The worker reads those copies, not the live product row. That way an
attempt always knows exactly which version of the user's request it is working
on, even if the product row has changed since.

### Revision

A counter on the work row. It goes up every time the captured input changes for
the same owner. Due records the **desired** revision (the latest input) and the
**applied** revision (the input whose result was saved). An attempt that started
on revision 1 cannot save its result once revision 2 has been requested.

### Fencing token

A random value stored on the work row and replaced every time a worker claims
it. The worker keeps a copy of the token it received. When it tries to save a
result, Due compares the copies. If another attempt has claimed the row since,
the tokens differ and the old attempt is turned away. "Fencing" is the general
name for this technique: shutting out a participant that has lost its right to
act, even if it does not know yet.

### Effect

The part of a job that does something outside the database transaction:
calling an API, writing a file, computing a value. Due runs your effect hook
*outside* any transaction, so it can take a long time and it can be interrupted
halfway. It may also run more than once.

### Settlement

The short database transaction at the end of an attempt that records the
result: it updates the product row and marks the work row finished. Due locks
the rows and checks the [fencing token](#fencing-token) and [revision](#revision)
*before* calling your settlement hook (`finalize_success()` and similar). That
check is what people mean by **guarded** or **fenced settlement**. Settlement
hooks should only write to the database, never call external systems.

### Retry budget

How many times Due will retry a failed attempt before giving up. Due calls each
of those retries a *rearm*; the default policy allows three. Work that runs out is **exhausted**; it is not
retried again until your application explicitly grants a new budget.

### Unknown outcome

What you know after a timeout: maybe the provider did the work, maybe it did
not. Treating "no reply" as "failed" and retrying is how customers are charged
twice. For operations that must not repeat, Due records the attempt as
**unknown** *before* calling the provider, and it never resends an unknown
attempt on its own.

### Evidence and receipt

Proof of what the provider actually did, such as an order ID it returned or a
record you can look up later. A **receipt** is the provider's answer, stored as
**evidence**. Once Due has evidence that an operation happened, it can finish
settlement without calling the provider again. "Nothing found" is usually *not*
evidence that nothing happened; a delayed request might still land.

### Projection and repair

A **projection** is a copy of product data kept in another system: a search
index, a cache, a CDN. External systems usually accept writes in whatever order
they arrive, so an old write can land after a new one. **Repair** means
writing the current value again later, until Due can be sure no older write is
still on its way.

### Compensation

Cleanup that must happen even if the process that created the mess dies. Due
records the cleanup as its own owed work *before* the step that creates the
temporary output, so another worker can finish it later.

### Universe

A label naming which tenant a work row belongs to, for applications that serve
several customers from one database. Due restores it when the work runs.
Single-tenant applications leave it empty (`""`), which is the default.

### Capability

The Python object that ties one kind of work together: the work model, its
owner, your hooks, and a policy (lease length, retries). You build it once at
import time, for example `calculate = DoubleNumber.for_owner(work=CalculationWork)`.
Your application calls it to request work, and Due's workers and recovery call
it to run and settle that work. Each chapter of the tutorial introduces one
kind of capability.

[Back to the tutorial](tutorial.md)
