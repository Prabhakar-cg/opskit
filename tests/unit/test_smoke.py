"""Smoke tests for the opskit package skeleton."""

from __future__ import annotations

from typer.testing import CliRunner

import opskit
from opskit.cli import app

runner = CliRunner()


def test_version_constant_is_set() -> None:
    assert opskit.__version__


def test_cli_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "opskit" in result.stdout


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "opskit" in result.stdout
