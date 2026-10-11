from pytest_obligation import configure
from pytest_obligation.integrations.celery import celery_publications
from pytest_obligation.integrations.django import django_host

configure(django_host(production_packages={"due", "examples"}, publication_recorder=celery_publications))
