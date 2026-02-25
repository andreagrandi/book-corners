import nox

nox.options.default_venv_backend = "uv"
nox.options.error_on_missing_interpreters = True

PYTHON_VERSIONS = ["3.14"]


@nox.session(python=PYTHON_VERSIONS)
def tests(session: nox.Session) -> None:
    """Run the test suite."""
    session.install("-r", "requirements.txt")
    session.run("pytest", *session.posargs)
