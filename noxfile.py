import os
import subprocess
import sys
from pathlib import Path

import nox

nox.options.default_venv_backend = "uv"
nox.options.error_on_missing_interpreters = True

PYTHON_VERSIONS = ["3.14"]

LOCAL_ENV_SCRIPT = Path(__file__).resolve().parent / "scripts" / "local_env.py"


def _configure_database(session: nox.Session) -> None:
    """Point the session at a reachable PostGIS database.
    CI keeps its ambient env; locally the dockerized db is started and discovered."""
    if os.environ.get("CI"):
        return
    result = subprocess.run(
        [sys.executable, str(LOCAL_ENV_SCRIPT), "ensure-db"],
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        database_url = result.stdout.strip()
        session.env["DATABASE_URL"] = database_url
        session.log(f"Using local dockerized PostGIS at {database_url}")
        return
    if os.environ.get("DATABASE_URL"):
        session.warn(
            "Could not start the dockerized db; falling back to ambient "
            f"DATABASE_URL ({os.environ['DATABASE_URL']})"
        )
        return
    session.error(
        "No database available: start Docker (nox boots the db service "
        "automatically) or set DATABASE_URL to a running PostGIS instance"
    )


@nox.session(python=PYTHON_VERSIONS)
def tests(session: nox.Session) -> None:
    """Run the test suite (excludes browser E2E tests)."""
    _configure_database(session)
    session.install("-r", "requirements.txt")
    session.run("python", "manage.py", "migrate", "--run-syncdb")
    session.run("python", "manage.py", "createcachetable")
    session.run("pytest", "-n", "auto", "-m", "not e2e", *session.posargs)


@nox.session(python=PYTHON_VERSIONS)
def e2e(session: nox.Session) -> None:
    """Run end-to-end browser tests with Playwright."""
    _configure_database(session)
    session.install("-r", "requirements.txt")
    session.run("playwright", "install", "chromium", "--with-deps")
    session.run("python", "manage.py", "migrate", "--run-syncdb")
    session.run("python", "manage.py", "collectstatic", "--noinput")
    session.run(
        "pytest",
        "tests/e2e/",
        "-m",
        "e2e",
        *session.posargs,
        env={"DJANGO_ALLOW_ASYNC_UNSAFE": "true"},
    )


@nox.session(python=PYTHON_VERSIONS)
def validate_openapi(session: nox.Session) -> None:
    """Validate the generated OpenAPI schema against the specification."""
    _configure_database(session)
    session.install("-r", "requirements.txt")
    session.run("python", "manage.py", "migrate", "--run-syncdb")
    session.run(
        "bash",
        "-c",
        "python manage.py export_openapi_schema > /tmp/openapi.json",
        external=True,
    )
    session.run(
        "python",
        "-c",
        "from openapi_spec_validator import validate; "
        "import json; "
        "validate(json.load(open('/tmp/openapi.json'))); "
        "print('OpenAPI schema is valid.')",
    )
