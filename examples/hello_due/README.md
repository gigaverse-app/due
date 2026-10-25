# Your first background task

Follow the [tutorial](../../package/docs/quickstart.md) to submit a greeting,
start its worker, inspect the saved result, then kill and restart the worker.
The app deliberately only sleeps, prints and returns a string.

[tasks.py](tasks.py) is the application behavior. The supplied [settings](settings.py),
[startup registration](apps.py) and [Celery app](celery.py) let you run it without
assembling a Django project. This project installs only the greeting app and Due;
it shares development service connection settings with the other examples.

The tutorial owns the commands and their expected output. For configuration in
an existing project, use the [Django Tasks setup guide](../../package/docs/django-tasks-quickstart.md).

## Verification

With the root development environment and PostgreSQL/Redis running:

```bash
uv run pytest integration_tests/examples/test_hello_due.py -q
```

This test uses its own queue and pytest's database. It starts the prepared
project in a separate Python process, applies its migrations, submits work before
starting a worker, and checks the printed greeting and persisted result. It then
kills the actual worker process during a ten-second task, restarts it, starts
Beat, and waits for real lease expiry and recovery. It also checks rollback and
recovery after dropping an immediate notification.

No lease timestamps are edited, and the test does not call recovery directly.
The test uses the same `solo` worker pool as the tutorial. Use a normal Celery
worker pool for production concurrency and worker-enforced execution time limits.
The library's separate prefork tests cover its worker-loss behavior.
