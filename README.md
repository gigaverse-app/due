![Due: An attempt can end. The obligation remains.](https://raw.githubusercontent.com/gigaverse-app/due/main/docs/assets/banner.png?v=e6e1fea2a5af)

# Due
[![CI](https://github.com/gigaverse-app/due/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/gigaverse-app/due/actions/workflows/ci.yml)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue)](pyproject.toml)
[![Django 6](https://img.shields.io/badge/Django-6-092E20?logo=django)](package/docs/integration.md)
[![Typed](https://img.shields.io/badge/typing-py.typed-blue)](package/src/due/py.typed)
[![Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](package/LICENSE)
[![Documentation](https://readthedocs.org/projects/due/badge/?version=latest)](https://due.readthedocs.io/en/latest/)
[![dry-lint](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fgigaverse-app%2Fdue%2Fmain%2F.dry-lint%2Fprogress.json)](#duplicates-kept-out)
[![pytest-obligation](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fgigaverse-app%2Fdue%2Fmain%2F.github%2Fobligation-badge.json)](#obligations-kept)

**An attempt can end. The obligation remains.**

[Start the tutorial](package/docs/quickstart.md) · [All eight chapters](package/docs/tutorial.md) · [Choose an API](package/docs/api-guide.md) · [Documentation](https://due.readthedocs.io/en/latest/)

The PyPI distribution name is **`django-due`** (`pip install django-due`).
Python imports remain **`due`**. See [releasing](RELEASING.md) for the manual release process.

## The database committed. The worker never heard.

You save a document and queue its publication. The broker is unavailable. Or the
worker writes the file and dies before recording success. Or a slow worker
returns after a newer revision has already finished.

**Due keeps track of what your Django application still owes.** It records the
obligation in PostgreSQL alongside the product change, uses Celery to attempt it,
and recovers work after lost messages and dead workers. Captured ownership tokens
and revisions prevent an old worker from settling a newer obligation.

```mermaid
flowchart LR
    A[Application transaction] --> B[(PostgreSQL: product + obligation)]
    A -. after commit .-> C[Celery worker]
    B --> D[Periodic recovery]
    D --> C
    C --> E[External effect]
    E --> F[Fenced local settlement]
    F --> B
```

The message gets work started sooner. The database remembers what remains owed.

[When Due is the right tool: strengths, tradeoffs, and alternatives →](docs/why-due.md)

## Start here: run and recover a background task

The [first tutorial](package/docs/quickstart.md) uses one deliberately trivial
function: sleep, print a greeting, return it. A prepared project supplies setup.

Define the task in `examples/hello_due/tasks.py`:

```python
import os
from time import sleep

from django.conf import settings
from django.tasks import task


@task(queue_name=settings.CELERY_TASK_DEFAULT_QUEUE)
def greet(name: str, seconds: int = 1) -> str:
    print(f"Starting greeting for {name} in process {os.getpid()}", flush=True)
    sleep(seconds)
    message = f"Hello, {name}!"
    print(message, flush=True)
    return message
```

Then submit it from application code or a Django shell:

```python
from examples.hello_due.tasks import greet

result = greet.enqueue("Ada")  # Returns immediately; a worker runs the function.

# After the worker finishes:
result.refresh()
print(result.return_value)  # Hello, Ada!
```

Submit a job before starting its worker. Read the saved result. Then kill a
worker during a slow task, restart it, and let Due recover that same job. Each
step shows the code, expected output and the behavior it demonstrates. The
[runnable example](examples/hello_due/README.md) has a test that performs the real
worker kill and waits for recurring recovery.

Import production types and startup helpers from `due.django.public`; common contract and fault
helpers live in `due.django.conformance`. [API choices, imports and file map →](package/docs/api-guide.md)

The [setup guide](package/docs/django-tasks-quickstart.md) explains how to add Due
to your Django project. The [eight-chapter tutorial](package/docs/tutorial.md)
then introduces each capability with small operations and observable results:

1. Run a task and recover it after worker death.
2. Capture input and guard completion with `ReplaySafeEffect`.
3. Keep a mutable external view current with `Projection`.
4. Move or cancel a deadline with `ScheduledAction`.
5. Preserve an uncertain outcome with `DurableExternalOperation`.
6. Resolve it by reading a receipt with `ObservedExternalOperation`.
7. Recover cleanup with `IndependentCompensation` and coordinate calls with `OwnedDurableWorkCapability`.
8. Compose stages, try completion inline and bind application contract tests.

Then follow [Acme Printworks](docs/acme-printworks-tutorial.md) from a Django task
to proof publication, portal repair, print ordering and cleanup. Its
[runnable app](examples/acme_printworks/README.md) includes application contracts.
Build those contracts alongside your first native capability: the real commands,
provider faults and customer-visible observations make recovery promises testable.

## What happens when things go wrong?

| Failure | Due's response |
| --- | --- |
| Commit succeeds; queue publication is lost | Recovery finds the work and reserves another delivery. |
| The broker delivers the same message twice | Only the captured owner can execute and settle it. |
| A worker dies or returns after its lease expires | Recovery handles the expiry; stale settlement is rejected. |
| Desired inputs change during execution | Revisions fence old settlement; the current target stays owed. |
| A non-repeatable request times out | Its outcome stays **UNKNOWN** until correlated evidence resolves it. |
| One recovery row always raises | Other identities progress; the failure is reported and held with backoff. |
| Retries are exhausted | Work remains visibly stopped for an explicit application decision. |

A local lease cannot cancel an external request. **Due does not promise
exactly-once external execution.** Your application defines replay safety,
provider idempotency, evidence, and success. [Guarantees and limits →](package/docs/architecture.md)

```text
commit product + obligation --> notify worker --> execute --> fenced settlement
             |                       X lost
             +------ recurring recovery --------> execute --> fenced settlement
```

[Follow a real obligation through message loss and process death →](docs/recovery-walkthrough.md)

## More than a task queue

Use Due when a business obligation must survive the attempt that tried to
fulfill it. Celery remains the transport; your application retains its own
models, transactions and product logic.

| What your application owes | Start with |
| --- | --- |
| A safely repeatable effect | `ReplaySafeEffect` |
| An external view of changing desired state | `Projection` |
| An action at a movable or cancellable deadline | `ScheduledAction` |
| A non-repeatable operation, such as a provider request with an uncertain outcome | `DurableExternalOperation` |
| Periodic observation of an uncertain operation | `ObservedExternalOperation` |
| Cleanup owed even if the original worker dies | `IndependentCompensation` |
| Fenced completion on a row with an existing lifecycle | [Completion leases](package/docs/completion-leases.md) |

The same work-table runtime also supports dependent wakes, discovery from
persisted facts, execution gates, continuations, status mirrors and captured
inputs. [Capability guide and executable examples →](package/docs/capabilities.md)

**Alpha, Django-backed.** Extracted from Gigaverse's work-table primitive; the API
may change before 1.0. Only work-table composition is public. Native-table
composition is excluded. [Verification scope and limits →](package/docs/verification.md)

## Coming from another library?

**Using Django Tasks?** Due includes an optional backend for `django.tasks`.
Keep `@task`, `.enqueue()` and `TaskResult` while adding transactional admission,
crash recovery, delayed tasks and named queues. [Setup and guarantees →](package/docs/django-tasks.md)

Start with [your library's migration guide](package/docs/migrations.md). Each guide
covers the benefits of adopting Due, a concrete migration, behavior to preserve,
cutover and rollback, and whether a compatibility layer would help. Guides cover
Procrastinate, DBOS, RQ, Django Tasks, Celery and the other Python libraries in
[the background-work ecosystem overview](docs/alternatives.md).

## Install

Requires Python 3.14, PostgreSQL and a Django 6 application. Celery needs a broker
and a running worker plus a recurring recovery scheduler (normally Celery Beat).
The integration suite uses PostgreSQL 17 and Redis 7.

Install the published package and commit the resolved version in your lockfile:

```bash
uv add 'django-due[redis]'
```

The documentation under `latest` follows main. Check your installed version
against the release notes before using an API added after that release.

Use the [task tutorial](package/docs/quickstart.md) and its prepared example.
They use source to include the consolidated imports added after
0.10.0. For an existing application, follow the [Django Tasks reference](package/docs/django-tasks.md)
or [native integration reference](package/docs/integration.md). Both need registration
in every process, workers and recurring recovery.

## Run the example

This quickstart runs the minimal [document-publication application](examples/documents/README.md).
The separate [Books application](examples/books/README.md) adapts Procrastinate’s
Django books demo into a Due workflow and documents its upstream provenance.

```bash
git clone https://github.com/gigaverse-app/due.git
cd due
uv sync --all-packages --locked
docker compose up -d
export DJANGO_SETTINGS_MODULE=examples.settings
uv run python -m django migrate
# Separate terminals, with the same settings:
uv run celery -A examples.celery_app:app worker --loglevel=INFO -Q due
uv run celery -A examples.celery_app:app beat --loglevel=INFO
```

Create a document from another terminal:

```bash
DJANGO_SETTINGS_MODULE=examples.settings uv run python -m django shell -c \
  'from examples.documents.models import Document; from examples.documents.effects import request_publication; document = Document.objects.create(); print(request_publication(document.pk, "Hello from Due").pk)'
```

The worker writes an immutable revision object under `/tmp/due-documents` and
publishes its key on the document row. Configure `DUE_DATABASE_URL`,
`DUE_BROKER_URL`, `DUE_OUTPUT_DIR` and `DUE_QUEUE` as needed. Compose credentials
are for local development only; both service ports bind to localhost.

## See recovery survive a lost message

For a browser application with two independently recoverable stages, run the
[books indexing demo](examples/books/README.md). See the
[example catalog](examples/README.md) for setup and contribution conventions.

With the local services running:

```bash
uv run pytest integration_tests/integration/test_celery_worker.py -v
```

This test commits a document and its publication obligation while withholding the
initial notification. It then starts a **separate Celery prefork worker**, sends
the recovery task through **Redis**, and checks the file and product state in
**PostgreSQL**. [Read the complete failure history](integration_tests/integration/test_celery_worker.py).

For application-bound recovery, replay, revision, admission and real process-death checks:

```bash
uv run pytest integration_tests/conformance -p due.django.conformance.reporting \
  --due-work-summary --due-work-profile-report=profiles.json --durable-work-report=effects.json
```

[pytest-obligation](https://github.com/gigaverse-app/pytest-obligation) generates
the applicable tests from a contract bound to Due's actual coordinator. CI
requires executed passing evidence for the claimed profiles; an empty or filtered
report fails. These proofs cover the bound example, not arbitrary application
callbacks. Enroll your own capability with `effect_contract_suite` and
`EffectExamples`: [authoring guide](package/docs/conformance.md),
[API and module map](package/docs/conformance-api.md),
[executable example](integration_tests/conformance/document_examples.py),
[verification scope](package/docs/verification.md).

## Inspect stopped work

```bash
uv run python -m django due_status
uv run python -m django due_status publish_document --limit 10
```

The read-only command reports exhausted, terminal-failed and unknown work,
unresolved attempts, and deferred recovery identities. It never authorizes a
replay. [Operations and monitoring →](package/docs/operations.md)

## Does Due keep its own promises?

Due's guarantees are checked the same way an adopter checks theirs, and so is its code.

### Obligations kept

Every week CI runs all of Due's conformance suites on `main`, the library's and the example applications',
with [pytest-obligation](https://github.com/gigaverse-app/pytest-obligation): lost messages, dead workers, stale
workers and retries, each against a real PostgreSQL. This is that run's report, kept current by a pull request
whenever the evidence changes.

<!-- pytest-obligation:start -->
### Due&#x27;s own conformance suites

<a href="https://github.com/gigaverse-app/pytest-obligation"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/sticky-banner-dark.svg"><img alt="pytest-obligation: what passed, what is missing, what to fix" src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/sticky-banner-light.svg" width="640"></picture></a>

### Obligation contract checks

Other pytest tests and workflow steps are not included in these counts.

<p><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/neutral-1-dark.svg"><img src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/neutral-1-light.svg" width="18" height="36" alt="17 applications" align="middle"></picture><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/neutral-7-dark.svg"><img src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/neutral-7-light.svg" width="18" height="36" alt="" align="middle"></picture><picture><img src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/space.svg" width="51" height="36" alt="" align="middle"></picture><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/good-1-dark.svg"><img src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/good-1-light.svg" width="18" height="36" alt="1132 contract checks passed" align="middle"></picture><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/good-1-dark.svg"><img src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/good-1-light.svg" width="18" height="36" alt="" align="middle"></picture><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/good-3-dark.svg"><img src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/good-3-light.svg" width="18" height="36" alt="" align="middle"></picture><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/good-2-dark.svg"><img src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/good-2-light.svg" width="18" height="36" alt="" align="middle"></picture><picture><img src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/space.svg" width="21" height="36" alt="" align="middle"></picture><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/divider-dark.svg"><img src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/divider-light.svg" width="1" height="36" alt="" align="middle"></picture><picture><img src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/space.svg" width="12" height="36" alt="" align="middle"></picture><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/good-0-dark.svg"><img src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/good-0-light.svg" width="18" height="36" alt="0 contract failures" align="middle"></picture><picture><img src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/space.svg" width="68" height="36" alt="" align="middle"></picture><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/good-0-dark.svg"><img src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/good-0-light.svg" width="18" height="36" alt="0 blocked checks" align="middle"></picture><picture><img src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/space.svg" width="75" height="36" alt="" align="middle"></picture><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/good-0-dark.svg"><img src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/good-0-light.svg" width="18" height="36" alt="0 coverage gaps" align="middle"></picture><picture><img src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/space.svg" width="72" height="36" alt="" align="middle"></picture><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/good-0-dark.svg"><img src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/good-0-light.svg" width="18" height="36" alt="0 rejected bindings" align="middle"></picture><picture><img src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/space.svg" width="73" height="36" alt="" align="middle"></picture><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/neutral-0-dark.svg"><img src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/neutral-0-light.svg" width="18" height="36" alt="0 skipped tests" align="middle"></picture><picture><img src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/space.svg" width="69" height="36" alt="" align="middle"></picture><br><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/captions-with-skips-dark.svg"><img src="https://raw.githubusercontent.com/gigaverse-app/pytest-obligation/v1/docs/assets/report-glyphs/captions-with-skips-light.svg" width="640" height="34" alt="applications · contract checks passed · contract failures · blocked checks · coverage gaps · rejected bindings · skipped tests" align="middle"></picture></p>

<details>
<summary>Application inventory and skipped tests</summary>

**17** applications with results · **0** applications not run
Checks skipped: **0**.

Application inventory was not supplied; uncollected suites cannot be listed.

</details>

### Applications

All collected applications, most actionable first. Expand the evidence below for reasons and tickets.

| Application | Passed | Failed | Blocked | Gaps | Rejected |
| :--- | ---: | ---: | ---: | ---: | ---: |
| index&#95;book | **48** | 0 | 0 | 0 | 0 |
| publish&#95;book | **43** | 0 | 0 | 0 | 0 |
| project&#95;catalog | **54** | 0 | 0 | 0 | 0 |
| Due replay-safe work tab&#8203;le | **87** | 0 | 0 | 0 | 0 |
| celery: a worker runs &#x27;p&#8203;ublish&#95;document&#x27; through&#8203; Celery | **1** | 0 | 0 | 0 | 0 |
| publish&#95;document | **50** | 0 | 0 | 0 | 0 |
| calculate&#95;number | **49** | 0 | 0 | 0 | 0 |
| print&#95;order | **49** | 0 | 0 | 0 | 0 |
| publish&#95;at&#95;deadline | **47** | 0 | 0 | 0 | 0 |
| checkout&#95;order | **55** | 0 | 0 | 0 | 0 |
| uuid&#95;work&#95;test | **46** | 0 | 0 | 0 | 0 |
| composed&#95;inline&#95;chunk&#95;se&#8203;lf&#95;test | **117** | 0 | 0 | 0 | 0 |
| composed&#95;chunk&#95;self&#95;test | **117** | 0 | 0 | 0 | 0 |
| batch&#95;self&#95;test | **106** | 0 | 0 | 0 | 0 |
| inline&#95;chunk&#95;self&#95;test | **114** | 0 | 0 | 0 | 0 |
| member&#95;sync&#95;self&#95;test | **43** | 0 | 0 | 0 | 0 |
| chunk&#95;self&#95;test | **106** | 0 | 0 | 0 | 0 |

### Evidence and decisions


<details>
<summary><strong>index&#95;book</strong> · evidence and decisions</summary>

Contract suite: <code>integration&#95;tests/conformance/test&#95;book&#95;contracts.py:6</code>

<details>
<summary><strong>⚪ Deliberate exclusions</strong></summary>

- **Uncertain outcomes are handled (C)** · Not applicable (application decision). Application reason: Replay-safe mode requires the separately generated actual external replay proof.
- **Results eventually settle correctly (E)** · Not applicable (application decision). Application reason: Indexing captures the submission once; operator retry preserves that input and its identity.
- **Owed work is discovered from stored facts (F)** · Not applicable (application decision). Application reason: No canonical discovery is registered.
- **Execution respects its gate (G)** · Not applicable (application decision). Application reason: The production capability has no execution gate.

These are scope decisions, not demonstrated defects. Review their reasons when the application changes.

</details>

---

<details>
<summary><strong>Hypothesis · No generated scenario bindings collected</strong></summary>

No optional schedule-exploration cases were collected for this suite. Other Hypothesis use inside application callbacks is not assessed here.

</details>

---

<details>
<summary><strong>🟢 6 supported guarantees</strong></summary>

- **Unfinished work gets recovered**
- **Worker ownership is bounded**
- **Needed records are retained**
- **Repeating work is harmless**
- **Intent and work are saved together**
- **Retries have a limit**

Suite (application code): <code>integration&#95;tests.conformance.test&#95;book&#95;contracts.TestBookIndexing</code>

</details>

</details>

<details>
<summary><strong>publish&#95;book</strong> · evidence and decisions</summary>

Contract suite: <code>integration&#95;tests/conformance/test&#95;book&#95;contracts.py:11</code>

<details>
<summary><strong>⚪ Deliberate exclusions</strong></summary>

- **Uncertain outcomes are handled (C)** · Not applicable (application decision). Application reason: Replay-safe mode requires the separately generated actual external replay proof.
- **Results eventually settle correctly (E)** · Not applicable (application decision). Application reason: Publication captures the upstream result key; later metadata and repeated settlement cannot replace it.
- **Owed work is discovered from stored facts (F)** · Not applicable (application decision). Application reason: No canonical discovery is registered.
- **Execution respects its gate (G)** · Not applicable (application decision). Application reason: The production capability has no execution gate.
- **Intent and work are saved together (I)** · Not applicable (application decision). Application reason: Publication admission writes one successor row; the upstream handoff is covered by index&#95;book process histories and the interrupted-settlement journey.

These are scope decisions, not demonstrated defects. Review their reasons when the application changes.

</details>

---

<details>
<summary><strong>Hypothesis · No generated scenario bindings collected</strong></summary>

No optional schedule-exploration cases were collected for this suite. Other Hypothesis use inside application callbacks is not assessed here.

</details>

---

<details>
<summary><strong>🟢 5 supported guarantees</strong></summary>

- **Unfinished work gets recovered**
- **Worker ownership is bounded**
- **Needed records are retained**
- **Repeating work is harmless**
- **Retries have a limit**

Suite (application code): <code>integration&#95;tests.conformance.test&#95;book&#95;contracts.TestBookPublication</code>

</details>

</details>

<details>
<summary><strong>project&#95;catalog</strong> · evidence and decisions</summary>

Contract suite: <code>integration&#95;tests/conformance/test&#95;catalog&#95;contract.py:10</code>

<details>
<summary><strong>⚪ Deliberate exclusions</strong></summary>

- **Uncertain outcomes are handled (C)** · Not applicable (application decision). Application reason: Replay-safe mode requires the separately generated actual external replay proof.
- **Results eventually settle correctly (E) / IN&#95;FLIGHT** · Not applicable (application decision). Application reason: This catalog overwrites one provider key; late-write repair and captured revisions own convergence, not a monotonic merge or dependency gate.
- **Results eventually settle correctly (E) / EVIDENCE&#95;CONFLUENCE** · Not applicable (application decision). Application reason: This catalog overwrites one provider key; late-write repair and captured revisions own convergence, not a monotonic merge or dependency gate.
- **Owed work is discovered from stored facts (F)** · Not applicable (application decision). Application reason: No canonical discovery is registered.
- **Execution respects its gate (G)** · Not applicable (application decision). Application reason: The production capability has no execution gate.

These are scope decisions, not demonstrated defects. Review their reasons when the application changes.

</details>

---

<details>
<summary><strong>Hypothesis · No generated scenario bindings collected</strong></summary>

No optional schedule-exploration cases were collected for this suite. Other Hypothesis use inside application callbacks is not assessed here.

</details>

---

<details>
<summary><strong>🟢 7 supported guarantees</strong></summary>

- **Unfinished work gets recovered**
- **Worker ownership is bounded**
- **Needed records are retained**
- **Results eventually settle correctly**
- **Repeating work is harmless**
- **Intent and work are saved together**
- **Retries have a limit**

Suite (application code): <code>integration&#95;tests.conformance.test&#95;catalog&#95;contract.TestCatalogProjection</code>

</details>

</details>

<details>
<summary><strong>Due replay-safe work tab&#8203;le</strong> · evidence and decisions</summary>

Contract contract setup: <code>integration&#95;tests/conformance/test&#95;contract.py:14</code>

<details>
<summary><strong>⚪ Deliberate exclusions</strong></summary>

- **Uncertain outcomes are handled (C)** · Not applicable (application decision). Application reason: This example is replay-safe; evidence-required operations have their own regression tests.
- **Results eventually settle correctly (E)** · Not applicable (application decision). Application reason: This example has an immutable target; projection revision tests exercise mutable targets.
- **Owed work is discovered from stored facts (F)** · Not applicable (application decision). Application reason: This example admits explicit obligations and declares no fact-derived discovery.
- **Execution respects its gate (G)** · Not applicable (application decision). Application reason: This example has no prerequisite gate; eligibility has separate regression tests.
- **Repeating work is harmless (H)** · Not applicable (application decision). Application reason: This example&#x27;s external call returns a value without a remote mutation.

These are scope decisions, not demonstrated defects. Review their reasons when the application changes.

</details>

---

<details>
<summary><strong>Hypothesis · No generated scenario bindings collected</strong></summary>

No optional schedule-exploration cases were collected for this suite. Other Hypothesis use inside application callbacks is not assessed here.

</details>

---

<details>
<summary><strong>🟢 5 supported guarantees</strong></summary>

- **Unfinished work gets recovered**
- **Worker ownership is bounded**
- **Needed records are retained**
- **Intent and work are saved together**
- **Retries have a limit**

Suite (application code): <code>integration&#95;tests.conformance.test&#95;contract.TestWorkTable</code>

</details>

</details>

<details>
<summary><strong>celery: a worker runs &#x27;p&#8203;ublish&#95;document&#x27; through&#8203; Celery</strong> · evidence and decisions</summary>

Contract contract setup: <code>integration&#95;tests/conformance/test&#95;document&#95;celery&#95;worker.py:60</code>

<details>
<summary><strong>⚪ Deliberate exclusions</strong></summary>

- **Unfinished work gets recovered (A)** · Not claimed by application. Application reason: what the worker loses is recovered by Due&#x27;s recovery of &#x27;publish&#95;document&#x27;, whose own contract proves its recovery; these histories run it after the worker restarts and check that every failure converges
- **Worker ownership is bounded (B)** · Not claimed by application. Application reason: a task&#x27;s lease is the broker&#x27;s visibility timeout or connection, not a fenced claim Celery can check
- **Uncertain outcomes are handled (C)** · Not claimed by application. Application reason: with task&#95;acks&#95;late a task whose worker died may run again; whether it reached its service is its own
- **Needed records are retained (D)** · Not applicable (application decision). Application reason: stored results are the record of settled work, not work still owed
- **Results eventually settle correctly (E)** · Not applicable (application decision). Application reason: one worker settles each delivery; no two results race to write it
- **Owed work is discovered from stored facts (F)** · Not applicable (application decision). Application reason: the obligation is the message itself, not a fact derived from product state
- **Execution respects its gate (G)** · Not claimed by application. Application reason: Celery&#x27;s worker contract has no application readiness predicate; dependencies and product gates need their own ExecutionGate binding, including release with its notification lost.
- **Repeating work is harmless (H)** · Not claimed by application. Application reason: a task can run more than once (with task&#95;acks&#95;late, a redelivery after its worker was lost), so rerunning it must be safe, but that is a property of each task&#x27;s function, not of Celery
- **Intent and work are saved together (I)** · Not claimed by application. Application reason: Celery&#x27;s worker receives already admitted work; it cannot make an application&#x27;s product transaction atomic with enqueue. Bind Profile I on the application&#x27;s command contract.
- **Retries have a limit (J)** · Not claimed by application. Application reason: a task&#x27;s retries are its own (self.retry, autoretry&#95;for); a redelivery after a lost worker is not counted as one

These are scope decisions, not demonstrated defects. Review their reasons when the application changes.

</details>

---

<details>
<summary><strong>Hypothesis · No generated scenario bindings collected</strong></summary>

No optional schedule-exploration cases were collected for this suite. Other Hypothesis use inside application callbacks is not assessed here.

</details>

---

<details>
<summary><strong>⚪ 0 supported guarantees</strong></summary>


Suite (application code): <code>integration&#95;tests.conformance.test&#95;document&#95;celery&#95;worker.TestPublicationThroughCelery</code>

</details>

</details>

<details>
<summary><strong>publish&#95;document</strong> · evidence and decisions</summary>

Contract suite: <code>integration&#95;tests/conformance/test&#95;document&#95;contract.py:11</code>

<details>
<summary><strong>⚪ Deliberate exclusions</strong></summary>

- **Uncertain outcomes are handled (C)** · Not applicable (application decision). Application reason: Replay-safe mode requires the separately generated actual external replay proof.
- **Results eventually settle correctly (E) / MONOTONIC&#95;RESULTS** · Not applicable (application decision). Application reason: File revisions have separate immutable keys; only a fenced pointer is settled.
- **Results eventually settle correctly (E) / IN&#95;FLIGHT** · Not applicable (application decision). Application reason: File revisions have separate immutable keys; only a fenced pointer is settled.
- **Results eventually settle correctly (E) / EVIDENCE&#95;CONFLUENCE** · Not applicable (application decision). Application reason: File revisions have separate immutable keys; only a fenced pointer is settled.
- **Owed work is discovered from stored facts (F)** · Not applicable (application decision). Application reason: No canonical discovery is registered.
- **Execution respects its gate (G)** · Not applicable (application decision). Application reason: The production capability has no execution gate.

These are scope decisions, not demonstrated defects. Review their reasons when the application changes.

</details>

---

<details>
<summary><strong>Hypothesis · No generated scenario bindings collected</strong></summary>

No optional schedule-exploration cases were collected for this suite. Other Hypothesis use inside application callbacks is not assessed here.

</details>

---

<details>
<summary><strong>🟢 7 supported guarantees</strong></summary>

- **Unfinished work gets recovered**
- **Worker ownership is bounded**
- **Needed records are retained**
- **Results eventually settle correctly**
- **Repeating work is harmless**
- **Intent and work are saved together**
- **Retries have a limit**

Suite (application code): <code>integration&#95;tests.conformance.test&#95;document&#95;contract.TestDocumentPublication</code>

</details>

</details>

<details>
<summary><strong>calculate&#95;number</strong> · evidence and decisions</summary>

Contract suite: <code>integration&#95;tests/conformance/test&#95;number&#95;contract.py:18</code>

<details>
<summary><strong>⚪ Deliberate exclusions</strong></summary>

- **Uncertain outcomes are handled (C)** · Not applicable (application decision). Application reason: Replay-safe mode requires the separately generated actual external replay proof.
- **Results eventually settle correctly (E) / MONOTONIC&#95;RESULTS** · Not applicable (application decision). Application reason: Arithmetic has no mutable remote state; only the fenced product result is published.
- **Results eventually settle correctly (E) / IN&#95;FLIGHT** · Not applicable (application decision). Application reason: Arithmetic has no mutable remote state; only the fenced product result is published.
- **Results eventually settle correctly (E) / EVIDENCE&#95;CONFLUENCE** · Not applicable (application decision). Application reason: Arithmetic has no mutable remote state; only the fenced product result is published.
- **Owed work is discovered from stored facts (F)** · Not applicable (application decision). Application reason: No canonical discovery is registered.
- **Execution respects its gate (G)** · Not applicable (application decision). Application reason: The production capability has no execution gate.

These are scope decisions, not demonstrated defects. Review their reasons when the application changes.

</details>

---

<details>
<summary><strong>Hypothesis · No generated scenario bindings collected</strong></summary>

No optional schedule-exploration cases were collected for this suite. Other Hypothesis use inside application callbacks is not assessed here.

</details>

---

<details>
<summary><strong>🟢 7 supported guarantees</strong></summary>

- **Unfinished work gets recovered**
- **Worker ownership is bounded**
- **Needed records are retained**
- **Results eventually settle correctly**
- **Repeating work is harmless**
- **Intent and work are saved together**
- **Retries have a limit**

Suite (application code): <code>integration&#95;tests.conformance.test&#95;number&#95;contract.TestNumberCalculation</code>

</details>

</details>

<details>
<summary><strong>print&#95;order</strong> · evidence and decisions</summary>

Contract suite: <code>integration&#95;tests/conformance/test&#95;order&#95;contract.py:6</code>

<details>
<summary><strong>⚪ Deliberate exclusions</strong></summary>

- **Needed records are retained (D)** · Not applicable (application decision). Application reason: Registered policy retains work and evidence indefinitely.
- **Results eventually settle correctly (E)** · Not applicable (application decision). Application reason: A submitted print order is immutable and cannot become a different physical order.
- **Owed work is discovered from stored facts (F)** · Not applicable (application decision). Application reason: No canonical discovery is registered.
- **Execution respects its gate (G)** · Not applicable (application decision). Application reason: The production capability has no execution gate.
- **Repeating work is harmless (H)** · Not claimed by application. Application reason: Production requires definite provider evidence; another call can duplicate an occurrence.

These are scope decisions, not demonstrated defects. Review their reasons when the application changes.

</details>

---

<details>
<summary><strong>Hypothesis · No generated scenario bindings collected</strong></summary>

No optional schedule-exploration cases were collected for this suite. Other Hypothesis use inside application callbacks is not assessed here.

</details>

---

<details>
<summary><strong>🟢 5 supported guarantees</strong></summary>

- **Unfinished work gets recovered**
- **Worker ownership is bounded**
- **Uncertain outcomes are handled**
- **Intent and work are saved together**
- **Retries have a limit**

Suite (application code): <code>integration&#95;tests.conformance.test&#95;order&#95;contract.TestPrintOrder</code>

</details>

</details>

<details>
<summary><strong>publish&#95;at&#95;deadline</strong> · evidence and decisions</summary>

Contract suite: <code>integration&#95;tests/conformance/test&#95;scheduled&#95;contract.py:6</code>

<details>
<summary><strong>⚪ Deliberate exclusions</strong></summary>

- **Uncertain outcomes are handled (C)** · Not applicable (application decision). Application reason: Replay-safe mode requires the separately generated actual external replay proof.
- **Needed records are retained (D)** · Not applicable (application decision). Application reason: Registered policy retains work and evidence indefinitely.
- **Results eventually settle correctly (E) / MONOTONIC&#95;RESULTS** · Not applicable (application decision). Application reason: A pending deadline is replaced or cancelled; immutable revision files and a fenced product pointer need no merge or dependency gate.
- **Results eventually settle correctly (E) / IN&#95;FLIGHT** · Not applicable (application decision). Application reason: A pending deadline is replaced or cancelled; immutable revision files and a fenced product pointer need no merge or dependency gate.
- **Results eventually settle correctly (E) / EVIDENCE&#95;CONFLUENCE** · Not applicable (application decision). Application reason: A pending deadline is replaced or cancelled; immutable revision files and a fenced product pointer need no merge or dependency gate.
- **Owed work is discovered from stored facts (F)** · Not applicable (application decision). Application reason: No canonical discovery is registered.
- **Execution respects its gate (G)** · Not applicable (application decision). Application reason: The production capability has no execution gate.

These are scope decisions, not demonstrated defects. Review their reasons when the application changes.

</details>

---

<details>
<summary><strong>Hypothesis · No generated scenario bindings collected</strong></summary>

No optional schedule-exploration cases were collected for this suite. Other Hypothesis use inside application callbacks is not assessed here.

</details>

---

<details>
<summary><strong>🟢 6 supported guarantees</strong></summary>

- **Unfinished work gets recovered**
- **Worker ownership is bounded**
- **Results eventually settle correctly**
- **Repeating work is harmless**
- **Intent and work are saved together**
- **Retries have a limit**

Suite (application code): <code>integration&#95;tests.conformance.test&#95;scheduled&#95;contract.TestScheduledPublication</code>

</details>

</details>

<details>
<summary><strong>checkout&#95;order</strong> · evidence and decisions</summary>

Contract suite: <code>integration&#95;tests/conformance/test&#95;widget&#95;contract.py:6</code>

<details>
<summary><strong>⚪ Deliberate exclusions</strong></summary>

- **Uncertain outcomes are handled (C)** · Not applicable (application decision). Application reason: Replay-safe mode requires the separately generated actual external replay proof.
- **Needed records are retained (D)** · Not applicable (application decision). Application reason: Registered policy retains work and evidence indefinitely.
- **Results eventually settle correctly (E)** · Not applicable (application decision). Application reason: A checkout captures one submission key; catalog edits and retries never replace it.
- **Owed work is discovered from stored facts (F)** · Not applicable (application decision). Application reason: No canonical discovery is registered.
- **Repeating work is harmless (H)** · Not applicable (application decision). Application reason: DatabaseEffect commits its callback with the work fence; the separate delivery replay proof checks that boundary, not raw external-call idempotency.

These are scope decisions, not demonstrated defects. Review their reasons when the application changes.

</details>

---

<details>
<summary><strong>Hypothesis · No generated scenario bindings collected</strong></summary>

No optional schedule-exploration cases were collected for this suite. Other Hypothesis use inside application callbacks is not assessed here.

</details>

---

<details>
<summary><strong>🟢 5 supported guarantees</strong></summary>

- **Unfinished work gets recovered**
- **Worker ownership is bounded**
- **Execution respects its gate**
- **Intent and work are saved together**
- **Retries have a limit**

Suite (application code): <code>integration&#95;tests.conformance.test&#95;widget&#95;contract.TestWidgetCheckout</code>

</details>

</details>

<details>
<summary><strong>uuid&#95;work&#95;test</strong> · evidence and decisions</summary>

Contract suite: <code>package/tests/uuid&#95;work/test&#95;contract.py:86</code>

<details>
<summary><strong>⚪ Deliberate exclusions</strong></summary>

- **Needed records are retained (D)** · Not applicable (application decision). Application reason: Registered policy retains work and evidence indefinitely.
- **Results eventually settle correctly (E)** · Not applicable (application decision). Application reason: The publication captures its payload once; changing owner fields cannot revise that immutable occurrence.
- **Owed work is discovered from stored facts (F)** · Not applicable (application decision). Application reason: No canonical discovery is registered.
- **Execution respects its gate (G)** · Not applicable (application decision). Application reason: The production capability has no execution gate.
- **Repeating work is harmless (H)** · Not claimed by application. Application reason: Production requires definite provider evidence; another call can duplicate an occurrence.
- **Intent and work are saved together (I)** · Not applicable (application decision). Application reason: The owner already exists; admission commits one new work row atomically.

These are scope decisions, not demonstrated defects. Review their reasons when the application changes.

</details>

---

<details>
<summary><strong>Hypothesis · No generated scenario bindings collected</strong></summary>

No optional schedule-exploration cases were collected for this suite. Other Hypothesis use inside application callbacks is not assessed here.

</details>

---

<details>
<summary><strong>🟢 4 supported guarantees</strong></summary>

- **Unfinished work gets recovered**
- **Worker ownership is bounded**
- **Uncertain outcomes are handled**
- **Retries have a limit**

Suite (application code): <code>test&#95;contract.TestUUIDOperationContract</code>

</details>

</details>

<details>
<summary><strong>composed&#95;inline&#95;chunk&#95;se&#8203;lf&#95;test</strong> · evidence and decisions</summary>

Contract suite: <code>package/tests/bulk/test&#95;composed&#95;work&#95;contract.py:33</code>

<details>
<summary><strong>⚪ Deliberate exclusions</strong></summary>

- **Needed records are retained (D)** · Not applicable (application decision). Application reason: Registered policy retains work and evidence indefinitely.
- **Results eventually settle correctly (E) / STALE&#95;SNAPSHOTS** · Not applicable (application decision). Application reason: A fixed batch refuses new inputs; no desired revision can supersede its captured membership.
- **Results eventually settle correctly (E) / IN&#95;FLIGHT** · Not applicable (application decision). Application reason: This immutable cohort has no competing desired-state writer; receipt actors are covered by evidence confluence.
- **Owed work is discovered from stored facts (F)** · Not applicable (application decision). Application reason: No canonical discovery is registered.
- **Execution respects its gate (G)** · Not applicable (application decision). Application reason: The production capability has no execution gate.
- **Repeating work is harmless (H)** · Not claimed by application. Application reason: Production requires definite provider evidence; another call can duplicate an occurrence.
- **Intent and work are saved together (I)** · Not applicable (application decision). Application reason: The producer inserts business intent and coordination in one row; the rollback control proves they cannot split.

These are scope decisions, not demonstrated defects. Review their reasons when the application changes.

</details>

---

<details>
<summary><strong>Hypothesis · Not run</strong></summary>

Generated schedule exploration uses the application's scenario bindings.

- Application scenario <code>bulk-partial-receipts</code>: **Disabled or filtered out**
- Application scenario <code>bulk-receipts-across-subset-retry</code>: **Disabled or filtered out**

These counts are scenario checks, not generated examples. Example counts, shrinking statistics and replay histories are not recorded in this report; inspect the pytest log for a failing history.

</details>

---

<details>
<summary><strong>🟢 5 supported guarantees</strong></summary>

- **Unfinished work gets recovered**
- **Worker ownership is bounded**
- **Uncertain outcomes are handled**
- **Results eventually settle correctly**
- **Retries have a limit**

Suite (application code): <code>tests.bulk.test&#95;composed&#95;work&#95;contract.TestComposedInlineReceiptContract</code>

</details>

</details>

<details>
<summary><strong>composed&#95;chunk&#95;self&#95;test</strong> · evidence and decisions</summary>

Contract suite: <code>package/tests/bulk/test&#95;composed&#95;work&#95;contract.py:18</code>

<details>
<summary><strong>⚪ Deliberate exclusions</strong></summary>

- **Needed records are retained (D)** · Not applicable (application decision). Application reason: Registered policy retains work and evidence indefinitely.
- **Results eventually settle correctly (E) / STALE&#95;SNAPSHOTS** · Not applicable (application decision). Application reason: A fixed batch refuses new inputs; no desired revision can supersede its captured membership.
- **Results eventually settle correctly (E) / IN&#95;FLIGHT** · Not applicable (application decision). Application reason: This immutable cohort has no competing desired-state writer; receipt actors are covered by evidence confluence.
- **Owed work is discovered from stored facts (F)** · Not applicable (application decision). Application reason: No canonical discovery is registered.
- **Execution respects its gate (G)** · Not applicable (application decision). Application reason: The production capability has no execution gate.
- **Repeating work is harmless (H)** · Not claimed by application. Application reason: Production requires definite provider evidence; another call can duplicate an occurrence.
- **Intent and work are saved together (I)** · Not applicable (application decision). Application reason: The producer inserts business intent and coordination in one row; the rollback control proves they cannot split.

These are scope decisions, not demonstrated defects. Review their reasons when the application changes.

</details>

---

<details>
<summary><strong>Hypothesis · Not run</strong></summary>

Generated schedule exploration uses the application's scenario bindings.

- Application scenario <code>bulk-partial-receipts</code>: **Disabled or filtered out**
- Application scenario <code>bulk-receipts-across-subset-retry</code>: **Disabled or filtered out**

These counts are scenario checks, not generated examples. Example counts, shrinking statistics and replay histories are not recorded in this report; inspect the pytest log for a failing history.

</details>

---

<details>
<summary><strong>🟢 5 supported guarantees</strong></summary>

- **Unfinished work gets recovered**
- **Worker ownership is bounded**
- **Uncertain outcomes are handled**
- **Results eventually settle correctly**
- **Retries have a limit**

Suite (application code): <code>tests.bulk.test&#95;composed&#95;work&#95;contract.TestComposedSeparateReceiptContract</code>

</details>

</details>

<details>
<summary><strong>batch&#95;self&#95;test</strong> · evidence and decisions</summary>

Contract suite: <code>package/tests/bulk/test&#95;contract.py:116</code>

<details>
<summary><strong>⚪ Deliberate exclusions</strong></summary>

- **Needed records are retained (D)** · Not applicable (application decision). Application reason: Registered policy retains work and evidence indefinitely.
- **Results eventually settle correctly (E) / STALE&#95;SNAPSHOTS** · Not applicable (application decision). Application reason: A fixed batch refuses new inputs; no desired revision can supersede its captured membership.
- **Results eventually settle correctly (E) / IN&#95;FLIGHT** · Not applicable (application decision). Application reason: This immutable cohort has no competing desired-state writer; receipt actors are covered by evidence confluence.
- **Owed work is discovered from stored facts (F)** · Not applicable (application decision). Application reason: No canonical discovery is registered.
- **Execution respects its gate (G)** · Not applicable (application decision). Application reason: The production capability has no execution gate.
- **Repeating work is harmless (H)** · Not claimed by application. Application reason: Production requires definite provider evidence; another call can duplicate an occurrence.
- **Intent and work are saved together (I)** · Not applicable (application decision). Application reason: The owner exists before admission; admission commits one new work row atomically.

These are scope decisions, not demonstrated defects. Review their reasons when the application changes.

</details>

---

<details>
<summary><strong>Hypothesis · Not run</strong></summary>

Generated schedule exploration uses the application's scenario bindings.

- Application scenario <code>bulk-partial-receipts</code>: **Disabled or filtered out**
- Application scenario <code>bulk-receipts-across-subset-retry</code>: **Disabled or filtered out**

These counts are scenario checks, not generated examples. Example counts, shrinking statistics and replay histories are not recorded in this report; inspect the pytest log for a failing history.

</details>

---

<details>
<summary><strong>🟢 5 supported guarantees</strong></summary>

- **Unfinished work gets recovered**
- **Worker ownership is bounded**
- **Uncertain outcomes are handled**
- **Results eventually settle correctly**
- **Retries have a limit**

Suite (application code): <code>tests.bulk.test&#95;contract.TestBulkOperationContract</code>

</details>

</details>

<details>
<summary><strong>inline&#95;chunk&#95;self&#95;test</strong> · evidence and decisions</summary>

Contract suite: <code>package/tests/bulk/test&#95;inline&#95;work&#95;contract.py:29</code>

<details>
<summary><strong>⚪ Deliberate exclusions</strong></summary>

- **Needed records are retained (D)** · Not applicable (application decision). Application reason: Registered policy retains work and evidence indefinitely.
- **Results eventually settle correctly (E) / STALE&#95;SNAPSHOTS** · Not applicable (application decision). Application reason: A fixed batch refuses new inputs; no desired revision can supersede its captured membership.
- **Results eventually settle correctly (E) / IN&#95;FLIGHT** · Not applicable (application decision). Application reason: This immutable cohort has no competing desired-state writer; receipt actors are covered by evidence confluence.
- **Owed work is discovered from stored facts (F)** · Not applicable (application decision). Application reason: No canonical discovery is registered.
- **Execution respects its gate (G)** · Not applicable (application decision). Application reason: The production capability has no execution gate.
- **Repeating work is harmless (H)** · Not claimed by application. Application reason: Production requires definite provider evidence; another call can duplicate an occurrence.
- **Intent and work are saved together (I)** · Not applicable (application decision). Application reason: The producer inserts business intent and coordination in one row; the rollback control proves they cannot split.

These are scope decisions, not demonstrated defects. Review their reasons when the application changes.

</details>

---

<details>
<summary><strong>Hypothesis · Not run</strong></summary>

Generated schedule exploration uses the application's scenario bindings.

- Application scenario <code>bulk-partial-receipts</code>: **Disabled or filtered out**
- Application scenario <code>bulk-receipts-across-subset-retry</code>: **Disabled or filtered out**

These counts are scenario checks, not generated examples. Example counts, shrinking statistics and replay histories are not recorded in this report; inspect the pytest log for a failing history.

</details>

---

<details>
<summary><strong>🟢 5 supported guarantees</strong></summary>

- **Unfinished work gets recovered**
- **Worker ownership is bounded**
- **Uncertain outcomes are handled**
- **Results eventually settle correctly**
- **Retries have a limit**

Suite (application code): <code>tests.bulk.test&#95;inline&#95;work&#95;contract.TestInlineOperationContract</code>

</details>

</details>

<details>
<summary><strong>member&#95;sync&#95;self&#95;test</strong> · evidence and decisions</summary>

Contract suite: <code>package/tests/bulk/test&#95;replay&#95;safe&#95;contract.py:121</code>

<details>
<summary><strong>⚪ Deliberate exclusions</strong></summary>

- **Uncertain outcomes are handled (C)** · Not applicable (application decision). Application reason: Replay-safe mode requires the separately generated actual external replay proof.
- **Needed records are retained (D)** · Not applicable (application decision). Application reason: Registered policy retains work and evidence indefinitely.
- **Results eventually settle correctly (E)** · Not applicable (application decision). Application reason: A cohort&#x27;s member keys are captured at admission; a later sync admits a new cohort.
- **Owed work is discovered from stored facts (F)** · Not applicable (application decision). Application reason: No canonical discovery is registered.
- **Execution respects its gate (G)** · Not applicable (application decision). Application reason: The production capability has no execution gate.
- **Intent and work are saved together (I)** · Not applicable (application decision). Application reason: The owner exists before admission; admission commits one new work row atomically.

These are scope decisions, not demonstrated defects. Review their reasons when the application changes.

</details>

---

<details>
<summary><strong>Hypothesis · No generated scenario bindings collected</strong></summary>

No optional schedule-exploration cases were collected for this suite. Other Hypothesis use inside application callbacks is not assessed here.

</details>

---

<details>
<summary><strong>🟢 4 supported guarantees</strong></summary>

- **Unfinished work gets recovered**
- **Worker ownership is bounded**
- **Repeating work is harmless**
- **Retries have a limit**

Suite (application code): <code>tests.bulk.test&#95;replay&#95;safe&#95;contract.TestReplaySafeBulkContract</code>

</details>

</details>

<details>
<summary><strong>chunk&#95;self&#95;test</strong> · evidence and decisions</summary>

Contract suite: <code>package/tests/bulk/test&#95;self&#95;work&#95;contract.py:13</code>

<details>
<summary><strong>⚪ Deliberate exclusions</strong></summary>

- **Needed records are retained (D)** · Not applicable (application decision). Application reason: Registered policy retains work and evidence indefinitely.
- **Results eventually settle correctly (E) / STALE&#95;SNAPSHOTS** · Not applicable (application decision). Application reason: A fixed batch refuses new inputs; no desired revision can supersede its captured membership.
- **Results eventually settle correctly (E) / IN&#95;FLIGHT** · Not applicable (application decision). Application reason: This immutable cohort has no competing desired-state writer; receipt actors are covered by evidence confluence.
- **Owed work is discovered from stored facts (F)** · Not applicable (application decision). Application reason: No canonical discovery is registered.
- **Execution respects its gate (G)** · Not applicable (application decision). Application reason: The production capability has no execution gate.
- **Repeating work is harmless (H)** · Not claimed by application. Application reason: Production requires definite provider evidence; another call can duplicate an occurrence.
- **Intent and work are saved together (I)** · Not applicable (application decision). Application reason: The producer inserts business intent and coordination in one row; the rollback control proves they cannot split.

These are scope decisions, not demonstrated defects. Review their reasons when the application changes.

</details>

---

<details>
<summary><strong>Hypothesis · Not run</strong></summary>

Generated schedule exploration uses the application's scenario bindings.

- Application scenario <code>bulk-partial-receipts</code>: **Disabled or filtered out**
- Application scenario <code>bulk-receipts-across-subset-retry</code>: **Disabled or filtered out**

These counts are scenario checks, not generated examples. Example counts, shrinking statistics and replay histories are not recorded in this report; inspect the pytest log for a failing history.

</details>

---

<details>
<summary><strong>🟢 5 supported guarantees</strong></summary>

- **Unfinished work gets recovered**
- **Worker ownership is bounded**
- **Uncertain outcomes are handled**
- **Results eventually settle correctly**
- **Retries have a limit**

Suite (application code): <code>tests.bulk.test&#95;self&#95;work&#95;contract.TestSelfContainedOperationContract</code>

</details>

</details>

---

<details>
<summary><strong>What do these numbers mean?</strong></summary>

- **Failures:** an executed assertion failed unexpectedly or was an application-declared expected failure. Expected failures still permit successful CI; the visible breakdown distinguishes them.
- **Rejected bindings:** repair the connection to production code; these checks provide no supporting evidence.
- **Blocked checks:** checks could not establish a result, including missing execution, setup/cleanup errors, binding-debt or unclassified expected failures, and unexpected passes requiring declaration review.
- **Coverage gaps:** guarantees not assessed, missing scenarios, or with declared gaps. Each affected guarantee counts once. Deliberate exclusions are shown separately.

Pass/fail/blocked/rejected counts partition unique checks. Gaps count guarantees, not extra tests. Declined and not-applicable declarations remain separate and do not establish passing coverage. Graphics color contract results: passing checks and zero issues are green, failures and rejected bindings are red, blocked checks and gaps are amber. Missing reports never show green zeroes. The workflow status above is separate from these contract counts.

Elapsed time covers the pytest session. Summed check time includes setup, execution and cleanup; parallel work overlaps.
Application totals include discovered declarations and reported suites. Skipped checks count known deselections and explicit pytest skips; incomplete selected checks are not skips. Uncollected applications have unknown check counts. Zero recorded outcomes do not certify passing coverage.

Application names, quoted reasons and diagnostic values come from application code. Guarantee labels come from pytest-obligation. Results cover collected suites only; passing checks do not rate every test's quality.

Test step (reported): **success**
Source: <code>weekly complete CI run on main</code>

</details>

[Report guide](https://github.com/gigaverse-app/pytest-obligation/blob/v1/package/docs/github-action-report.md)
<!-- pytest-obligation:end -->

### Duplicates kept out

A pre-commit hook and the required `dry-lint` check block code that re-implements a function Due already has.
Duplication that predates adoption is an accepted backlog that only shrinks: the weekly audit records each
pair a refactor removes. See [CONTRIBUTING.md](CONTRIBUTING.md#duplication).

<!-- dry-lint-progress:start -->
<a href="https://github.com/gigaverse-app/dry-lint"><picture><source media="(prefers-color-scheme: dark)" srcset="https://dry-lint-counter.gigaverse-oss.workers.dev/v1/sign.svg?theme=dark&amp;key=35f6ba0c499a9204cc6a7aa502ed349c208027ccd99edbf9287eae3bf6bb181d&amp;name=gigaverse-app%2Fdue&amp;cleaned=11&amp;remaining=18&amp;dismissed=0"><img alt="dry-lint in gigaverse-app/due: new duplicates kept out of its pull requests, 11 old duplicates cleaned up, 18 remaining" src="https://dry-lint-counter.gigaverse-oss.workers.dev/v1/sign.svg?key=35f6ba0c499a9204cc6a7aa502ed349c208027ccd99edbf9287eae3bf6bb181d&amp;name=gigaverse-app%2Fdue&amp;cleaned=11&amp;remaining=18&amp;dismissed=0" width="100%"></picture></a>

**Reviewed duplication debt** · Counts from the committed baselines.

| Accepted pairs | Remaining | Retired | Dismissed |
| :-- | --: | --: | --: |
| Functions | 18 | 11 | 0 |
| Models | Not initialized | — | — |

[Accepted backlog](.dry-lint/baseline.json) · [Weekly audit](https://github.com/gigaverse-app/due/actions/workflows/dry-lint-audit.yml)

Retired means a side was removed, or (functions only) the judge now finds the pair separate. It is not a count of refactors. Dismissals stay separate; an uninitialized model baseline is unknown.
<!-- dry-lint-progress:end -->

## Documentation

| You want to… | Read |
| --- | --- |
| Run a task and recover it after worker failure | [First task tutorial](package/docs/quickstart.md) |
| Learn each capability step by step | [All eight tutorial chapters](package/docs/tutorial.md) |
| Configure Due in a Django project | [Setup guide](package/docs/django-tasks-quickstart.md) |
| Learn native admission and completion hooks | [Native work lesson](package/docs/native-work-tutorial.md) |
| Find the high-level API and imports | [API guide and file map](package/docs/api-guide.md) |
| Add richer product guarantees | [Printworks tutorial](docs/acme-printworks-tutorial.md) |
| Decide whether Due fits your application | [Strengths and tradeoffs](docs/why-due.md), [alternatives](docs/alternatives.md) |
| Integrate the runtime | [Django and Celery wiring](package/docs/integration.md), [capabilities](package/docs/capabilities.md) |
| Move an existing application onto Due | [Migration settings and stable wire names](package/docs/integration.md#migrating-an-existing-application), [library migration guides](package/docs/migrations.md) |
| Prove your application's obligations | [Conformance walkthrough](package/docs/conformance.md), [checked immutable inputs](package/docs/conformance.md#prove-an-immutable-input-declaration), [executable document adopter](integration_tests/conformance/document_examples.py) |
| Find an interface or understand scope | [Conformance API, field reference and module boundaries](package/docs/conformance-api.md), [provider-seam factories](package/docs/conformance-api.md#derive-routine-bindings-from-one-provider-seam) |
| Understand a passing result | [Verification scope](package/docs/verification.md), [architecture and limits](package/docs/architecture.md) |
| Operate and recover work | [Operations](package/docs/operations.md), [failed-tick evidence](package/docs/operations.md#failed-infrequent-recovery) |
| Edit the library inside an adopter checkout | [Package ownership and subtree development](package/docs/development.md) |
| Migrate the backend harness | [Backend adoption and ownership](package/docs/backend-adoption.md) |

## Contribute

```bash
uv sync --all-packages --locked
uv run pre-commit install
uv run pre-commit run --all-files
docker compose up -d --wait
(cd package && ../.venv/bin/python -m pytest --due-work-check-fixtures)
uv run --no-sync pytest --due-work-require-assessed --due-work-profile-report=profiles.json
uv build --project package --out-dir dist
```

Pre-commit uses the Ruff and Pyrefly versions in `uv.lock`. CI also tests the
minimum Django version, checks migrations, and installs the wheel without test
dependencies. See [CONTRIBUTING.md](CONTRIBUTING.md) for the complete workflow.

[Integration](package/docs/integration.md) · [Architecture](package/docs/architecture.md) ·
[Conformance](package/docs/conformance.md) · [Recovery walkthrough](docs/recovery-walkthrough.md) ·
[Verification](package/docs/verification.md) · [Apache-2.0](package/LICENSE)

---

[![CI powered by CI-esta](https://img.shields.io/badge/CI_powered_by-CI--esta-EA580C?logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA2NCA2NCI%2BPHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiByeD0iMTUiIGZpbGw9IiNFQTU4MEMiLz48cGF0aCBkPSJNMTUgMzRsMTEgMTEgMjAtMjIiIGZpbGw9Im5vbmUiIHN0cm9rZT0iI2ZmZiIgc3Ryb2tlLXdpZHRoPSI3IiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz48cGF0aCBkPSJNNDYgN2gxMGwtMTAgMTBoMTAiIGZpbGw9Im5vbmUiIHN0cm9rZT0iI2ZmZiIgc3Ryb2tlLXdpZHRoPSIzLjUiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCIvPjwvc3ZnPg%3D%3D)](https://github.com/gigaverse-app/ci-esta)
[![Tests selected by pytest-selective](https://img.shields.io/badge/tests_selected_by-pytest--selective-2563EB)](https://github.com/gigaverse-app/pytest-selective)
[![Crash-tested with pytest-obligation](https://img.shields.io/badge/crash--tested_with-pytest--obligation-7C3AED)](https://github.com/gigaverse-app/pytest-obligation)
[![Duplication checked by dry-lint](https://img.shields.io/badge/duplication_checked_by-dry--lint-7E4FC9)](https://github.com/gigaverse-app/dry-lint)

<sub>Pull requests run only the test lanes a change can
affect, planned by CI-esta from the workflows, behind CI-esta's required-check gates; every run gets a CI-esta
[timing report](.github/workflows/observe-ci.yml). Due's conformance lane runs the
pytest-obligation tests generated from its contracts.</sub>

### How this project keeps CI fast

See the [CI toolkit integration](docs/ci-toolkit.md) for lane selection, validated
environment caches, required gates and real dependency critical-path reports.
