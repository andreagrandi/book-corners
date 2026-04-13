import nox

nox.options.default_venv_backend = "uv"
nox.options.error_on_missing_interpreters = True

PYTHON_VERSIONS = ["3.14"]


@nox.session(python=PYTHON_VERSIONS)
def tests(session: nox.Session) -> None:
    """Run the test suite (excludes browser E2E tests)."""
    session.install("-r", "requirements.txt")
    session.run("python", "manage.py", "migrate", "--run-syncdb")
    session.run("python", "manage.py", "createcachetable")
    session.run("pytest", "-n", "auto", "-m", "not e2e", *session.posargs)


@nox.session(python=PYTHON_VERSIONS)
def e2e(session: nox.Session) -> None:
    """Run end-to-end browser tests with Playwright."""
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
