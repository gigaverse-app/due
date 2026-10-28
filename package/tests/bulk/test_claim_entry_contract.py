"""Composed entry uses the same generated failure/recovery contracts."""

import pytest

from due.django.conformance.suite import effect_contract_suite
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.self_work import entry_chunk_delivery, entry_inline_chunk_delivery, registered_chunk
from tests.harness.self_work import entry_chunk_environment as entry_chunk_environment
from tests.harness.self_work import entry_inline_chunk_environment as entry_inline_chunk_environment
from tests.harness.self_work_contract import self_work_examples

with registered_chunk(entry_chunk_delivery):

    @pytest.mark.usefixtures("entry_chunk")
    @effect_contract_suite(entry_chunk_delivery, examples=self_work_examples(entry_chunk_delivery, "entry_chunk"))
    class TestEntrySeparateReceiptContract:
        pass


with registered_chunk(entry_inline_chunk_delivery):

    @pytest.mark.usefixtures("entry_inline_chunk")
    @effect_contract_suite(
        entry_inline_chunk_delivery, examples=self_work_examples(entry_inline_chunk_delivery, "entry_inline_chunk")
    )
    class TestEntryInlineReceiptContract:
        pass
