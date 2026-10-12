"""
Pure retry timing shared by durable capabilities and native work engines.

ExponentialDelay owns only bounded arithmetic. The caller supplies a zero-based
step and owns its counter, budget and permission to retry. This module imports
no Django runtime so native model/state modules can use it during app loading.
The durable-work package re-exports the same value; it has no second formula.

Design and counter conventions: docs/architecture.md.
"""

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True, slots=True)
class ExponentialDelay:
    """Double a positive initial delay by step, saturating at maximum."""

    initial: timedelta
    maximum: timedelta

    def __post_init__(self) -> None:
        if self.initial <= timedelta(0) or self.maximum < self.initial:
            raise ValueError("initial must be positive and maximum at least initial")

    def at(self, step: int) -> timedelta:
        """Return min(initial * 2**step, maximum) without overflowing timedelta."""
        if step < 0:
            raise ValueError("delay step must be nonnegative")
        if step >= (self.maximum // self.initial).bit_length():
            return self.maximum
        return self.initial * (1 << step)
