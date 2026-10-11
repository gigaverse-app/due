"""Run Procrastinate's real worker, optionally dying after an external commit."""

import sys
from unittest.mock import patch

import django
from pytest_obligation.process_histories import die_here

django.setup()

from django.core.management import call_command  # noqa: E402

from examples.books import search  # noqa: E402
from examples.comparison.tasks import QUEUE, create_book, recover_stalled  # noqa: E402


def main() -> None:
    put, publish = search.put, search.publish

    def write(entry: search.IndexEntry) -> str:
        result = put(entry)
        die_here("after index write")
        return result

    def activate(key: str) -> str:
        result = publish(key)
        die_here("after activation")
        return result

    if sys.argv[1] == "recover":
        recover_stalled.configure(task_kwargs={"timestamp": 0}).defer()
    else:
        create_book(sys.argv[1], "Process Author")
        die_here("after admission")
    with patch.object(search, "put", write), patch.object(search, "publish", activate):
        call_command("procrastinate", "worker", "--queues", QUEUE, "--one-shot", "--concurrency", "1")


if __name__ == "__main__":
    main()
