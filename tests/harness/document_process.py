"""Child process: real publication with abrupt death at external boundaries."""

import sys
from pathlib import Path
from unittest.mock import patch

import django
from pytest_obligation.process_histories import die_here

django.setup()

from due.django.testing import hold_nudges  # noqa: E402
from examples.documents.effects import publish_document, request_publication  # noqa: E402


def main() -> None:
    with hold_nudges():
        work = request_publication(int(sys.argv[1]), "process content")
    die_here("after admission")
    original = Path.write_text

    def write(path: Path, text: str, *args, **kwargs) -> int:
        die_here("before file write")
        result = original(path, text, *args, **kwargs)
        die_here("after file write")
        return result

    with patch.object(Path, "write_text", write):
        publish_document.attempt_execution(work.pk)


if __name__ == "__main__":
    main()
