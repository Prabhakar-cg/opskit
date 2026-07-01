"""Nox sessions: tests across the support matrix, lint, and type-checks."""

from __future__ import annotations

import nox

nox.options.default_venv_backend = "uv"
nox.options.reuse_existing_virtualenvs = True

PYTHON_VERSIONS = ["3.9", "3.10", "3.11", "3.12", "3.13"]


def _install(session: nox.Session) -> None:
    session.run_install(
        "uv",
        "sync",
        "--extra",
        "dev",
        env={"UV_PROJECT_ENVIRONMENT": session.virtualenv.location},
    )


@nox.session(python=PYTHON_VERSIONS)
def tests(session: nox.Session) -> None:
    _install(session)
    session.run("pytest")


@nox.session
def lint(session: nox.Session) -> None:
    _install(session)
    session.run("ruff", "check", ".")
    session.run("ruff", "format", "--check", ".")


@nox.session
def types(session: nox.Session) -> None:
    _install(session)
    session.run("mypy")
    session.run("pyright")
