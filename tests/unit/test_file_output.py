"""Tests for opskit.file.output: human rendering, including markup-injection escaping."""

from __future__ import annotations

from rich.console import Console

from opskit.file.models import (
    ConversionResult,
    LineEndingReport,
    StructuredFormat,
    ValidationResult,
)
from opskit.file.output import render_conversion, render_lineendings, render_validation


def _render(result: ValidationResult) -> str:
    console = Console(no_color=True, width=200, record=True)
    render_validation(result, console=console)
    return console.export_text()


def _render_lineendings(result: LineEndingReport) -> str:
    console = Console(no_color=True, width=200, record=True)
    render_lineendings(result, console=console)
    return console.export_text()


def _render_conversion(result: ConversionResult) -> str:
    console = Console(no_color=True, width=200, record=True)
    render_conversion(result, console=console)
    return console.export_text()


def test_render_valid_result():
    output = _render(
        ValidationResult(path="config.json", format=StructuredFormat.JSON, valid=True)
    )
    assert "config.json" in output
    assert "valid" in output


def test_render_invalid_result_includes_location_and_message():
    output = _render(
        ValidationResult(
            path="broken.json",
            format=StructuredFormat.JSON,
            valid=False,
            error_line=3,
            error_column=5,
            error_message="unexpected token",
        )
    )
    assert "broken.json" in output
    assert "3:5" in output
    assert "unexpected token" in output


def test_render_escapes_markup_injection_in_path():
    output = _render(
        ValidationResult(path="[bold]evil[/bold].json", format=None, valid=True)
    )
    # The literal brackets must survive as text, not be interpreted as rich markup.
    assert "[bold]evil[/bold].json" in output


def test_render_undetermined_format_shows_unknown():
    output = _render(ValidationResult(path="mystery.txt", format=None, valid=False))
    assert "unknown" in output


def test_render_lineendings_consistent():
    output = _render_lineendings(
        LineEndingReport(
            path="unix.txt", crlf_count=0, lf_count=5, cr_count=0, mixed=False
        )
    )
    assert "unix.txt" in output
    assert "consistent" in output
    assert "lf=5" in output


def test_render_lineendings_mixed():
    output = _render_lineendings(
        LineEndingReport(
            path="mixed.txt", crlf_count=1, lf_count=2, cr_count=1, mixed=True
        )
    )
    assert "mixed" in output
    assert "crlf=1" in output
    assert "cr=1" in output


def test_render_lineendings_escapes_markup_injection_in_path():
    output = _render_lineendings(
        LineEndingReport(
            path="[bold]evil[/bold].txt",
            crlf_count=0,
            lf_count=1,
            cr_count=0,
            mixed=False,
        )
    )
    assert "[bold]evil[/bold].txt" in output


def test_render_conversion_basic():
    output = _render_conversion(
        ConversionResult(path="script.sh", destination="script.sh", in_place=True)
    )
    assert "script.sh" in output
    assert "ok" in output


def test_render_conversion_shows_backup():
    output = _render_conversion(
        ConversionResult(
            path="script.sh",
            destination="script.sh",
            in_place=True,
            backup_path="script.sh.bak",
        )
    )
    assert "script.sh.bak" in output


def test_render_conversion_shows_lossy_reason():
    output = _render_conversion(
        ConversionResult(
            path="data.xml",
            destination="data.json",
            in_place=False,
            lossless=False,
            lossy_reason="XML mixed content has no lossless representation",
        )
    )
    assert "lossy" in output
    assert "XML mixed content" in output
