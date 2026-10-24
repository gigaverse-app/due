import pytest
from due.django.conformance.host import work_table_host
from pytest_obligation import configure

configure(work_table_host(production_packages={"acme"}))


@pytest.fixture(autouse=True)
def isolated_provider(settings, tmp_path):
    settings.PROVIDER_DB = tmp_path / "provider.sqlite3"
