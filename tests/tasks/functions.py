"""Importable task bodies, also executed by the process-worker tests."""


def add(left, right):
    return left + right


async def async_add(left, right):
    return left + right


def context_value(context, value):
    return {"value": value, "attempt": context.attempt, "id": context.task_result.id}


def fail(message):
    raise ValueError(message)


def invalid_return():
    return object()


def echo(value):
    return value


def die_once(context, path):
    import os
    from pathlib import Path

    marker = Path(path)
    if not marker.exists():
        marker.write_text("durable effect")
        os._exit(23)
    return {"value": marker.read_text(), "attempt": context.attempt}
