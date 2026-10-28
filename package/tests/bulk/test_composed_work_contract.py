"""The existing generated contracts govern explicitly composed work-table declarations."""

import pytest

from due.django.conformance.suite import effect_contract_suite
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.self_work import composed_chunk_delivery, composed_inline_chunk_delivery, registered_chunk
from tests.harness.self_work import composed_chunk_environment as composed_chunk_environment
from tests.harness.self_work import composed_inline_chunk_environment as composed_inline_chunk_environment
from tests.harness.self_work_contract import self_work_examples

with registered_chunk(composed_chunk_delivery):

    @pytest.mark.usefixtures("composed_chunk")
    @effect_contract_suite(
        composed_chunk_delivery, examples=self_work_examples(composed_chunk_delivery, "composed_chunk")
    )
    class TestComposedSeparateReceiptContract:
        pass


with registered_chunk(composed_inline_chunk_delivery):

    @pytest.mark.usefixtures("composed_inline_chunk")
    @effect_contract_suite(
        composed_inline_chunk_delivery,
        examples=self_work_examples(composed_inline_chunk_delivery, "composed_inline_chunk"),
    )
    class TestComposedInlineReceiptContract:
        pass
