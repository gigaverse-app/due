"""Probe controls are checked against independently committed provider state."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from due.django.conformance.provider import ProviderProbe
from examples.orders import provider
from examples.orders.provider import PrintReceipt

pytestmark = pytest.mark.no_db


@contextmanager
def printer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ProviderProbe[[str, str], PrintReceipt, str]]:
    monkeypatch.setenv("DUE_BOOK_INDEX", str(tmp_path / "printer.sqlite3"))
    probe: ProviderProbe[[str, str], PrintReceipt, str] = ProviderProbe(
        provider.submit, key=lambda reference, title: reference
    )
    with probe.patch(provider, "submit"):
        yield probe


def test_probe_counts_real_entries_and_preserves_keyword_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = provider.submit
    with printer(tmp_path, monkeypatch) as probe:
        receipt = provider.submit(reference="one", title="First")
        provider.submit("two", "Second")
        assert provider.receipt("one") == receipt
        assert probe.count("one") == probe.count("two") == 1
        assert probe.count("absent") == 0
        assert probe.total == 2
    assert provider.submit is original


@pytest.mark.parametrize("after", [False, True], ids=["rejected-before-send", "response-lost-after-commit"])
def test_fault_location_controls_whether_a_real_order_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after: bool
) -> None:
    with printer(tmp_path, monkeypatch) as probe:
        fault = probe.lose_response if after else probe.reject
        with fault(lambda: TimeoutError("injected"), when=lambda key: key == "one"):
            with pytest.raises(TimeoutError, match="injected"):
                provider.submit("one", "First")
            provider.submit("two", "Unaffected")
        assert (provider.receipt("one") is not None) is after
        assert provider.receipt("two") is not None
        assert probe.count("one") == probe.count("two") == 1
        provider.submit("three", "Restored")
        assert provider.receipt("three") is not None


def test_nested_faults_and_patch_restore_after_exceptions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = provider.submit
    with pytest.raises(RuntimeError, match="body"):
        with printer(tmp_path, monkeypatch) as probe:
            with probe.reject(lambda: OSError("outer")):
                with probe.lose_response(lambda: TimeoutError("inner")):
                    with pytest.raises(TimeoutError, match="inner"):
                        provider.submit("one", "Committed")
                with pytest.raises(OSError, match="outer"):
                    provider.submit("two", "Rejected")
            assert provider.receipt("one") is not None
            assert provider.receipt("two") is None
            raise RuntimeError("body")
    assert provider.submit is original


def test_provider_exception_is_preserved_even_when_response_loss_is_armed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with printer(tmp_path, monkeypatch) as probe:
        monkeypatch.setenv("DUE_PRINTER_REJECT", "1")
        with probe.lose_response(lambda: TimeoutError("must not replace actual failure")):
            with pytest.raises(provider.PrinterRejected):
                provider.submit("one", "Rejected")
        assert provider.receipt("one") is None
        assert probe.count("one") == 1


def test_probe_refuses_a_different_target() -> None:
    probe = ProviderProbe(provider.submit, key=lambda reference, title: reference)
    with pytest.raises(ValueError, match="target"):
        with probe.patch(provider, "observe"):
            pytest.fail("wrong provider target was accepted")


def test_probe_does_not_invent_a_provider_effect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUE_BOOK_INDEX", str(tmp_path / "printer.sqlite3"))

    def broken_submit(reference: str, title: str) -> PrintReceipt:
        return PrintReceipt(order_id=999, reference=reference, title=title)

    monkeypatch.setattr(provider, "submit", broken_submit)
    probe = ProviderProbe(provider.submit, key=lambda reference, title: reference)
    with probe.patch(provider, "submit"), probe.lose_response(lambda: TimeoutError("lost")):
        with pytest.raises(TimeoutError):
            provider.submit("one", "Not actually committed")
        assert probe.count("one") == 1
        assert provider.receipt("one") is None


def test_class_level_method_probe_preserves_receiver_and_real_file(tmp_path: Path) -> None:
    original = Path.write_text
    probe = ProviderProbe(Path.write_text, key=lambda path, *args, **kwargs: path)
    destination = tmp_path / "actual.txt"
    with probe.patch(Path, "write_text"):
        assert destination.write_text("actual bytes", encoding="utf-8") == len("actual bytes")
        assert destination.read_text() == "actual bytes"
        assert probe.count(destination) == 1
    assert Path.write_text is original


def test_async_callable_is_rejected_before_creating_an_unawaited_coroutine() -> None:
    async def send(reference: str) -> str:
        return reference

    with pytest.raises(TypeError, match="synchronous"):
        ProviderProbe(send, key=lambda reference: reference)


def test_callable_with_async_call_is_rejected() -> None:
    class AsyncProvider:
        async def __call__(self, reference: str) -> str:
            return reference

    with pytest.raises(TypeError, match="synchronous"):
        ProviderProbe(AsyncProvider(), key=lambda reference: reference)


def test_static_method_binding_is_preserved() -> None:
    class Provider:
        @staticmethod
        def send(reference: str) -> str:
            return reference

    probe = ProviderProbe(Provider.send, key=lambda reference: reference)
    with probe.patch(Provider, "send"):
        assert Provider.send("class") == "class"
        assert Provider().send("instance") == "instance"
        assert probe.total == 2
    assert Provider().send("restored") == "restored"
    assert probe.total == 2


def test_classmethod_is_refused_without_replacing_it() -> None:
    class Provider:
        @classmethod
        def send(cls, reference: str) -> str:
            return reference

    original = Provider.send
    probe = ProviderProbe(Provider.send, key=lambda reference: reference)
    with pytest.raises(TypeError, match="classmethod"):
        with probe.patch(Provider, "send"):
            pytest.fail("class method accepted")
    assert Provider.send == original


def test_bound_instance_method_retains_the_real_receiver(tmp_path: Path) -> None:
    class FileProvider:
        def send(self, reference: str) -> int:
            return (tmp_path / reference).write_text("real")

    client = FileProvider()
    probe = ProviderProbe(client.send, key=lambda reference: reference)
    with probe.patch(client, "send"):
        assert client.send("one") == 4
        assert (tmp_path / "one").read_text() == "real"
        assert probe.count("one") == 1
