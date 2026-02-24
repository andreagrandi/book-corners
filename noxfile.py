import nox

nox.options.default_venv_backend = "uv"


@nox.session
def tests(session: nox.Session) -> None:
    """Run the test suite."""
    session.install("-r", "requirements.txt")
    session.run("pytest", *session.posargs)
