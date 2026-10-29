"""Inline evidence runs the same generated histories as separate receipt storage."""

import pytest

from due.django.conformance.suite import effect_contract_suite
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.self_work import SendChunk, inline_chunk_delivery, registered_chunk
from tests.harness.self_work import inline_chunk_environment as inline_chunk_environment
from tests.harness.self_work_contract import self_work_examples


@pytest.fixture
def response_diagnostics(monkeypatch):
    """All generated failure histories also carry opaque provider metadata."""
    send = SendChunk.send_batch

    def respond(self, work, batch, payload):
        result = send(self, work, batch, payload)
        return result.model_copy(update={"diagnostics": {"request_id": str(batch.attempt_id)}})

    monkeypatch.setattr(SendChunk, "send_batch", respond)


with registered_chunk(inline_chunk_delivery):

    @pytest.mark.usefixtures("inline_chunk", "response_diagnostics")
    @effect_contract_suite(inline_chunk_delivery, examples=self_work_examples(inline_chunk_delivery, "inline_chunk"))
    class TestInlineOperationContract:
        pass
