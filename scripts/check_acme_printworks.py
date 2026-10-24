"""Run the standalone adopter against this checkout, with isolated service state."""

import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote, urlparse
from uuid import uuid4

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "examples/acme_printworks"


def main():
    url = os.environ["DUE_DATABASE_URL"]
    db = urlparse(url)
    name = "acme_" + uuid4().hex
    with psycopg.connect(url, autocommit=True) as admin, TemporaryDirectory(prefix="acme-check-") as directory:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        env = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "project.settings",
            "PYTHONPATH": str(APP),
            "PYTEST_ADDOPTS": "",
            "PGDATABASE": name,
            "PGHOST": db.hostname or "localhost",
            "PGPORT": str(db.port or 5432),
            "PGUSER": unquote(db.username or ""),
            "PGPASSWORD": unquote(db.password or ""),
            "CELERY_BROKER_URL": os.environ["DUE_BROKER_URL"],
            "ACME_QUEUE": name,
            "ACME_PROVIDER_DB": str(Path(directory) / "providers.sqlite3"),
        }
        reports = ROOT / "acme-reports"
        reports.mkdir(exist_ok=True)

        def run(*arguments):
            subprocess.run([sys.executable, *map(str, arguments)], cwd=APP, env=env, check=True, timeout=180)

        try:
            run("manage.py", "makemigrations", "--check", "--dry-run")
            run(
                "-m",
                "pytest",
                "-q",
                "-p",
                "due.django.conformance.reporting",
                "--due-work-require-assessed",
                f"--due-work-profile-report={reports / 'profiles.json'}",
                f"--durable-work-report={reports / 'effects.json'}",
            )
            for capability in ("acme_proof", "acme_portal", "acme_print", "acme_cleanup"):
                run(
                    ROOT / "scripts/check_adoption_report.py",
                    reports / "profiles.json",
                    "--contract",
                    capability,
                    "--effect-report",
                    reports / "effects.json",
                    "--effect",
                    capability,
                )
            run("run_demo.py")
        finally:
            for database in ("test_" + name, name):
                admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)))


if __name__ == "__main__":
    main()
