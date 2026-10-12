"""Every recovery phase names where its isolation is proven, and a named primitive proof exists."""

from pathlib import Path

import pytest

from due.django.conformance.recovery_isolation import PHASE_PROOFS, ProvenOnce
from due.django.runtime.metrics import RecoveryPhase

pytestmark = pytest.mark.no_db

_REPOSITORY = Path(__file__).resolve().parents[2]


def test_every_recovery_phase_declares_its_isolation_proof() -> None:
    # A new phase is a new loop: it runs nothing until someone says where its poisoned identity is proven.
    assert set(PHASE_PROOFS) == set(RecoveryPhase)


@pytest.mark.parametrize(
    "proof",
    [proof for proof in PHASE_PROOFS.values() if isinstance(proof, ProvenOnce)],
    ids=lambda proof: proof.test.rsplit("::", 1)[-1],
)
def test_a_phase_proven_once_names_a_test_that_exists(proof: ProvenOnce) -> None:
    path, test = proof.test.split("::")
    assert f"def {test}(" in (_REPOSITORY / path).read_text(encoding="utf-8")
