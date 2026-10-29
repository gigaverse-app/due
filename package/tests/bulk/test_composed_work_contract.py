"""The existing generated contracts govern explicitly composed work-table declarations."""

from dataclasses import replace

import pytest

from due.django.conformance.suite import effect_contract_suite
from tests.harness.bounded_settlement import bounded_settlement_proofs
from tests.harness.committed_evidence import committed_evidence_proofs
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.self_work import composed_chunk_delivery, composed_inline_chunk_delivery, registered_chunk
from tests.harness.self_work import composed_chunk_environment as composed_chunk_environment
from tests.harness.self_work import composed_inline_chunk_environment as composed_inline_chunk_environment
from tests.harness.self_work_contract import self_work_examples

with registered_chunk(composed_chunk_delivery):

    @pytest.mark.usefixtures("composed_chunk")
    @effect_contract_suite(
        composed_chunk_delivery,
        examples=replace(
            self_work_examples(composed_chunk_delivery, "composed_chunk"),
            extras=bounded_settlement_proofs(composed_chunk_delivery)
            + committed_evidence_proofs(composed_chunk_delivery),
        ),
    )
    class TestComposedSeparateReceiptContract:
        pass


with registered_chunk(composed_inline_chunk_delivery):

    @pytest.mark.usefixtures("composed_inline_chunk")
    @effect_contract_suite(
        composed_inline_chunk_delivery,
        examples=replace(
            self_work_examples(composed_inline_chunk_delivery, "composed_inline_chunk"),
            extras=bounded_settlement_proofs(composed_inline_chunk_delivery)
            + committed_evidence_proofs(composed_inline_chunk_delivery),
        ),
    )
    class TestComposedInlineReceiptContract:
        pass
