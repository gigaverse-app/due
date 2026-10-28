"""Legacy hooks observe saved progress; failures retain independently committed evidence."""

import pytest

from tests.harness.bulk_settlement import assert_settlement_boundaries
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.self_work import chunk_environment as chunk_environment
from tests.harness.self_work import inline_chunk_environment as inline_chunk_environment

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("fixture", ["self_chunk", "inline_chunk"])
@pytest.mark.parametrize("phase", ["batch", "success"])
def test_settlement_visibility_and_recovery(request: pytest.FixtureRequest, fixture: str, phase: str) -> None:
    assert_settlement_boundaries(request.getfixturevalue(fixture), fail_at=phase)
