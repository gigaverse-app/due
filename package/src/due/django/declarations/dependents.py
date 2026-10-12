"""Declare existing work that must re-read facts an upstream settlement commits.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

from collections.abc import Callable
from dataclasses import dataclass

from due.django.models import UniverseDurableWork


@dataclass(frozen=True, slots=True)
class Dependent[UpstreamT]:
    """
    ``via`` returns the dependent's work id, or None when no dependent is owed.

    A returned id whose row is missing raises: a settlement never commits
    without the handoff it owes. ``via`` runs inside settlement under the
    upstream owner/work locks; keep it to a database read.
    """

    work: type[UniverseDurableWork]
    via: Callable[[UpstreamT], int | None]
