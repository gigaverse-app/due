"""Whether an adopter holds its generated cost contracts; imports no suite or application code.

Every ``effect_contract_suite`` defines ``test_statement_cost`` cases, but an
application adopts them by committing pytest-obligation's cost baseline at its
pytest root. Until then the cases are deselected and left out of the suite's
expected proofs, so upgrading Due does not fail an application that never
recorded a cost. Once the file exists, every case runs and a missing entry fails.
"""

from pathlib import Path

from pytest_obligation.costs import COST_BASELINE

#: The generated cost case every enrolled suite defines, parametrized by path.
COST_CASE = "test_statement_cost"


def costs_adopted(root: Path) -> bool:
    return (root / COST_BASELINE).is_file()
