import nox

nox.options.default_venv_backend = "uv"
nox.options.error_on_missing_interpreters = True

PYTHON_VERSIONS = ["3.14"]


@nox.session(python=PYTHON_VERSIONS)
def tests(session: nox.Session) -> None:
    """Run the test suite."""
    session.install("-r", "requirements.txt")
    session.run("pytest", *session.posargs)


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
