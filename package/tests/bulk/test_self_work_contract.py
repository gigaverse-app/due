"""Existing domain receipt storage runs the shared UUID work contract."""

import pytest

from due.django.conformance.suite import effect_contract_suite
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.self_work import chunk_delivery, registered_chunk
from tests.harness.self_work import chunk_environment as chunk_environment
from tests.harness.self_work_contract import self_work_examples

with registered_chunk():

    @pytest.mark.usefixtures("self_chunk")
    @effect_contract_suite(chunk_delivery, examples=self_work_examples(chunk_delivery, "self_chunk"))
    class TestSelfContainedOperationContract:
        pass
