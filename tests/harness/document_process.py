"""Child process: real publication with abrupt death at external boundaries."""

import sys
from unittest.mock import patch

import django
from pytest_obligation.process_histories import die_here

django.setup()

from due.django.testing import hold_nudges  # noqa: E402
from examples.documents import storage  # noqa: E402
from examples.documents.effects import publish_document, request_publication  # noqa: E402


def main() -> None:
    with hold_nudges():
        work = request_publication(int(sys.argv[1]), "process content")
    die_here("after admission")
    original = storage.write_revision

    def write(text: str, operation_key: str) -> str:
        die_here("before file write")
        result = original(text, operation_key)
        die_here("after file write")
        return result

    with patch.object(storage, "write_revision", write):
        publish_document.attempt_execution(work.pk)


if __name__ == "__main__":
    main()
