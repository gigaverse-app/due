# Double a number with Due

The application is deliberately trivial: sleep, multiply by two, print, save the
answer. Read the [Due tutorial](../../package/docs/quickstart.md) for the concepts
and failure histories. This page supplies the prepared Django/Celery setup.

The application code is in two files: [models.py](models.py) and
[effects.py](effects.py). [lesson.py](lesson.py) runs four controlled histories
against real PostgreSQL. The example uses the repository's existing settings,
manifest and services, so you do not need to construct a Django project first.
Other examples are installed in that shared project, but are not part of this
lesson.

## Prepare once

Use Python 3.14, [uv](https://docs.astral.sh/uv/getting-started/installation/) and
Docker Compose. Run these commands in a shell, using WSL on Windows:

```bash
git clone https://github.com/gigaverse-app/due.git
cd due
uv sync --all-packages --locked
docker compose up -d --wait
uv run python -m django migrate --settings=examples.settings
uv run python -m django due_check --settings=examples.settings
```

These examples and consolidated imports follow release 0.10.0; use the source
checkout containing them. PostgreSQL and Redis use the loopback ports in the
root [compose.yaml](../../compose.yaml). To use your own **dedicated development**
services, set `DUE_DATABASE_URL` and `DUE_BROKER_URL` instead of starting Compose.
Tests need permission to create a test database. The credentials in Compose are
only for local development.

## Run the four lessons

Keep workers and Beat stopped for this command. Its fault controls intercept
transport in this process and advance the recovery clock; an independent worker
would race those controlled histories.

```bash
uv run python -m django shell --settings=examples.settings -c "from examples.numbers.lesson import run; run()"
```

It prints and asserts rollback, recovery after a dropped notification, replay
after an interrupted attempt, and rejection of an old answer after new input.
It creates fresh calculation rows on each run. The controlled worker-stop fault
is an exception at the provider boundary, not an OS process kill.

## Use real workers

In separate terminals at the repository root:

```bash
uv run celery -A examples.celery_app:app worker -Q due --concurrency=1 --loglevel=INFO
uv run celery -A examples.celery_app:app beat --loglevel=INFO
uv run python -m django shell --settings=examples.settings
```

In the shell:

```python
from examples.celery_app import app
from examples.numbers.effects import request_number
from examples.numbers.models import Calculation

sample = Calculation.objects.create()
work = request_number(sample.pk, 21)
```

The worker prints `double(21) = 42`. Look up the result after it finishes:

```python
sample.refresh_from_db()
print(sample.number, sample.result)  # 21 42
```

To see live recovery, stop **Beat only** and submit another input in the shell:

```python
from due.django.conformance import hold_nudges

with hold_nudges() as dropped:
    work = request_number(sample.pk, 7)

print(len(dropped))  # 1
sample.refresh_from_db()
print(sample.number, sample.result)  # 7 None
```

Restart Beat. The registered recovery runs every five seconds. After recovery and
execution, refresh the row again: `7 14`. You did not resubmit the request. A
polling interval is not a completion deadline; allow about a minute locally.

Stop the worker and Beat with Ctrl+C. Use `docker compose down` when finished.

## Run the application checks

The lesson tests, generated native contracts and live worker/Beat journey run in
the existing integration CI lane. Independent enrollment checks reject a deleted
contract, and CI validates its executed report.

```bash
uv run pytest integration_tests/examples/test_numbers_lessons.py integration_tests/conformance/test_number_contract.py integration_tests/integration/test_numbers_live.py integration_tests/conformance/test_conformance_enrollment.py -p due.django.conformance.reporting --due-work-check-fixtures --due-work-debt-baseline=integration_tests/conformance/debt_baseline.yaml --due-work-profile-report=number-profiles.json --durable-work-report=number-effects.json
uv run python scripts/check_adoption_report.py number-profiles.json --contract calculate_number --verified A B D E H I J --effect-report number-effects.json --effect calculate_number
```

The [bindings](../../integration_tests/conformance/number_examples.py) call the real
request functions and arithmetic, count actual calls, capture actual printed
output, and independently read the saved product result. Faults replace the
provider boundary, transport or clock. They do not implement Due's recovery or
settlement logic. The example establishes the contract for harmless arithmetic
and repeated printing; it makes no claim about replaying a payment or other
non-repeatable provider operation.
