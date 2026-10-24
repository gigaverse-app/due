---
title: Due documentation
description: Keep background work owed until your application finishes it.
---

![Due](../docs/assets/banner.svg)

# Keep background work owed until your application finishes it.

A database commit can succeed while its queue notification disappears. Due
stores a work row alongside the product change, retries delivery through Celery,
and fences which worker can publish completion. Your application owns the
meaning of success and whether an external operation is safe to repeat.

[Learn Due through four failures](../package/docs/quickstart.md){ .md-button .md-button--primary }
[Choose an API](../package/docs/api-guide.md){ .md-button }

## Start with one calculation

Use **`ReplaySafeEffect` and `capability.atomic()`** when repeatable work must
publish only the current requested result. The first tutorial deliberately uses
trivial arithmetic so the interesting behavior belongs to Due:

```text
Request 21 -> calculate 42 -> pause before settlement
Request  7 -> old attempt returns 42 -> Due rejects its stale result
Recovery -> calculate 14 -> publish the current result
```

The [tutorial](../package/docs/quickstart.md) teaches atomic admission, lost
notifications, interrupted attempts and stale completion. A prepared application
supplies setup; runnable histories and application contracts check the claims.
Production imports live in `due.django.public`; common testing imports live in
`due.django.conformance`. [Choose the API and find its imports](../package/docs/api-guide.md).

For ordinary background functions, use the separate
[Django Tasks walkthrough](../package/docs/django-tasks-quickstart.md).

## Install in a Django application

```bash
uv add 'django-due[redis]'
```

The distribution is **django-due**; Python imports use **due**. You need Python
3.14+, Django 6.0.8+ (below 7), PostgreSQL, Celery, a broker, workers and a recurring
recovery scheduler. The API is alpha. Review and lock the installed release.

```mermaid
flowchart LR
  A[Commit product + work] --> B[(PostgreSQL)]
  A -. notification .-> C[Celery worker]
  B --> D[Recurring recovery]
  D --> C
  C --> E[External effect]
  E --> F[Fenced product completion]
```

Follow the [Acme Printworks tutorial](../docs/acme-printworks-tutorial.md) for a
runnable Django Tasks application that grows into native Due capabilities. Always
build its application contract alongside the feature: the shared pytest-obligation
tests make recovery promises executable from the first native capability.

## Your next step

1. [Run the trivial tutorial](../package/docs/quickstart.md) to learn Due's native
   API and see its guarantees at each failure boundary.
2. [Follow Printworks](../docs/acme-printworks-tutorial.md) to apply the ideas
   to publication, projections and uncertain provider operations.
3. Use the [integration](../package/docs/integration.md),
   [capability](../package/docs/capabilities.md) and [testing](../package/docs/conformance.md)
   references while building your application.

Due does not provide exactly-once external execution or replace a workflow
engine's history. A stale worker can still reach an external provider; fencing
protects local settlement. Read [guarantees and limits](../package/docs/architecture.md)
and [when to keep your existing tool](../docs/why-due.md) before migrating.

## Already using a task queue?

[Choose your migration guide](../package/docs/migrations.md). Each explains the
source library's identities, retries, results and cutover; one shared native
recipe avoids copying a second recovery implementation into every application.

## See the application outcome

![Due Widget Store with a dispatched order](assets/widget-demo.png)

The [Widget Store example](../examples/widget_store/README.md) runs a Django UI,
PostgreSQL, Redis, Celery and scheduled recovery. This screenshot shows a local
simulated order; it is not a production reliability measurement. The crash
control requires the example's supervisor and is disabled in an ordinary
`runserver` session.

## Related tools and support

[Choose a related tool](ecosystem.md) when you also need test selection, CI
measurement or background-work guarantees. Each project works independently.

`latest` follows main and can contain APIs newer than your installed package.
Check the release notes for that version. [Report an issue](https://github.com/gigaverse-app/due/issues)
with a minimal reproducer, or read [the contribution guide](../CONTRIBUTING.md).
