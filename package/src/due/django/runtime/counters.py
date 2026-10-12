"""Claim and attempt counters that cannot fail the claim that increments them."""

#: The largest value of a PostgreSQL ``integer`` column: claim and attempt counters saturate there.
CLAIM_COUNTER_CEILING = 2_147_483_647


def saturating_increment(counter: int) -> int:
    """
    One more claim or attempt, held at the column's maximum.

    A counter at the maximum is corrupt or imported, never earned, and an
    increment past it would fail its claim on every tick. The work table's
    ``attempt_count``, ``claim_generation`` and ``rearm_count`` use this, and so
    does a native lifecycle that counts claims or attempts in an ``integer``
    column (an email chunk's ``claim_generation`` and ``attempt_count``,
    account cleanup's ``cleanup_attempt_count``); none of them fences with the
    counter, so a held value changes nothing else. A ``smallint`` counter needs
    its own ceiling or a budget check before it, as personal Chat's
    ``attempts`` has. A fencing revision is never saturated: two revisions
    must stay distinct.
    """
    return min(counter + 1, CLAIM_COUNTER_CEILING)
