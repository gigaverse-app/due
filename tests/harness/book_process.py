"""A real child process exits at each book workflow boundary."""

import sys
from unittest.mock import patch

import django
from pytest_obligation.process_histories import die_here

django.setup()

from due.django.testing import hold_nudges  # noqa: E402
from examples.books import search  # noqa: E402
from examples.books.effects import index_book, publish_book  # noqa: E402
from examples.books.models import IndexBookWork, PublishBookWork  # noqa: E402
from examples.books.services import create_book  # noqa: E402


def main() -> None:
    put, publish = search.put, search.publish

    def write(entry: search.IndexEntry) -> str:
        die_here("before index write")
        result = put(entry)
        die_here("after index write")
        return result

    def activate(key: str) -> str:
        die_here("before activation")
        result = publish(key)
        die_here("after activation")
        return result

    with hold_nudges(), patch.object(search, "put", write), patch.object(search, "publish", activate):
        book = create_book(sys.argv[1], "Process Author")
        die_here("after admission")
        assert index_book.attempt_execution(IndexBookWork.objects.get(owner=book).pk)
        die_here("after handoff")
        assert publish_book.attempt_execution(PublishBookWork.objects.get(owner=book).pk)


if __name__ == "__main__":
    main()
