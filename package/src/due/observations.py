"""Adopter observations of effects, separate from evidence storage and settlement.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AppliedEffectEvidence[ResultT]:
    """The declared effect is satisfied; result is its canonical application evidence."""

    result: ResultT


def effect_result[ResultT](observation: ResultT | AppliedEffectEvidence[ResultT]) -> ResultT:
    """Pass canonical results to existing validation, storage and settlement hooks."""
    if isinstance(observation, AppliedEffectEvidence):
        return observation.result
    return observation
