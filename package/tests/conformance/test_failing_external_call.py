"""``failing_external_call`` counts every provider entry, whatever the seam's arguments."""

from types import SimpleNamespace

import pytest

from due.django.conformance.suite import failing_external_call


def delete_renditions(source_key: str, *, bucket: str) -> None:
    """A provider seam keyed by a source, not by the work row."""


def test_a_seam_keyed_by_a_source_still_counts_its_entries() -> None:
    work = SimpleNamespace(pk=7)
    with failing_external_call(f"{__name__}.delete_renditions") as failure:
        assert failure.attempts(work) == 0
        for expected in (1, 2):
            with pytest.raises(OSError, match="injected provider outage"):
                delete_renditions("avatars/7.png", bucket="media")
            assert failure.attempts(work) == expected
