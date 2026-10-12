"""The broker clock and local response do not settle provider-owned requests."""

import pytest

from due.django.conformance.background import BackgroundWork


def test_accepted_grant_can_apply_after_successful_revocation() -> None:
    provider_access: set[str] = set()
    broker = BackgroundWork()
    grant = broker.accept_provider_request(lambda: provider_access.add("member"))
    with pytest.raises(TimeoutError):
        grant.lose_response()
    revoke = broker.accept_provider_request(lambda: provider_access.discard("member"))
    revoke.complete()
    assert not provider_access
    assert not grant.completed
    grant.complete()
    assert provider_access == {"member"}
    assert grant.completed


def test_definite_grant_completion_before_revocation_removes_access() -> None:
    provider_access: set[str] = set()
    broker = BackgroundWork()
    grant = broker.accept_provider_request(lambda: provider_access.add("member"))
    grant.complete()
    assert provider_access == {"member"}
    revoke = broker.accept_provider_request(lambda: provider_access.discard("member"))
    revoke.complete()
    assert not provider_access
    with pytest.raises(AssertionError, match="already completed"):
        grant.complete()


def test_failed_application_does_not_manufacture_completion() -> None:
    def unavailable() -> None:
        raise ConnectionError("Provider still unavailable")

    request = BackgroundWork().accept_provider_request(unavailable)
    with pytest.raises(ConnectionError):
        request.complete()
    assert not request.completed
