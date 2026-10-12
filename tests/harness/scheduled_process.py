"""Real scheduled command and worker, interrupted at publication boundaries."""

import os
import sys
from datetime import timedelta
from unittest.mock import patch
from uuid import UUID

import django
from pytest_obligation.process_histories import die_here

django.setup()

from django.utils import timezone  # noqa: E402

from due.django.testing import hold_nudges  # noqa: E402
from examples.documents import storage  # noqa: E402
from examples.scheduled.effects import publish_at_deadline  # noqa: E402
from examples.scheduled.models import PublicationDeadline  # noqa: E402
from examples.scheduled.services import create_publication  # noqa: E402
from tests.conformance.scheduled_examples import TEXT  # noqa: E402


def main() -> None:
    original = storage.write_revision

    def write(text: str, operation_key: str) -> str:
        die_here("before file write")
        # Explicit negative control: a successful provider reply with wrong bytes.
        if os.environ.get("DUE_TEST_CORRUPT_PROVIDER"):
            text = "wrong edition"
        result = original(text, operation_key)
        die_here("after file write")
        return result

    with hold_nudges(), patch.object(storage, "write_revision", write):
        publication = create_publication(TEXT, timezone.now() - timedelta(seconds=1), submission_key=UUID(sys.argv[1]))
        die_here("after admission")
        assert publish_at_deadline.attempt_execution(PublicationDeadline.objects.get(owner=publication).pk)
        die_here("after settlement")


if __name__ == "__main__":
    main()
