"""Tests for opskit.file.output: human rendering, including markup-injection escaping."""

from __future__ import annotations

from rich.console import Console

from opskit.file.models import (
    MISSING,
    ConversionResult,
    DiffEntry,
    DuplicateGroup,
    LineEndingReport,
    StatResult,
    StructuralDiffResult,
    StructuredFormat,
    ValidationResult,
)
from opskit.file.output import (
    render_conversion,
    render_diff,
    render_duplicates,
    render_lineendings,
    render_stat,
    render_validation,
)


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


def _render_diff(result: StructuralDiffResult) -> str:
    console = Console(no_color=True, width=200, record=True)
    render_diff(result, console=console)
    return console.export_text()


def _render_duplicates(groups: tuple[DuplicateGroup, ...]) -> str:
    console = Console(no_color=True, width=200, record=True)
    render_duplicates(groups, console=console)
    return console.export_text()


def test_render_diff_equivalent():
    output = _render_diff(StructuralDiffResult(left_path="a.json", right_path="b.json"))
    assert "equivalent" in output
    assert "a.json" in output
    assert "b.json" in output


def test_render_diff_shows_differing_key_path_and_values():
    output = _render_diff(
        StructuralDiffResult(
            left_path="a.json",
            right_path="b.json",
            differences=(
                DiffEntry(key_path="server.port", left_value=80, right_value=443),
            ),
        )
    )
    assert "differs" in output
    assert "server.port" in output
    assert "80" in output
    assert "443" in output


def test_render_diff_shows_missing_side():
    output = _render_diff(
        StructuralDiffResult(
            left_path="a.json",
            right_path="b.json",
            differences=(DiffEntry(key_path="b", left_value=2, right_value=MISSING),),
        )
    )
    assert "missing" in output


def test_render_diff_escapes_markup_injection_in_path():
    output = _render_diff(
        StructuralDiffResult(left_path="[bold]evil[/bold].json", right_path="b.json")
    )
    assert "[bold]evil[/bold].json" in output


def test_render_duplicates_no_groups():
    output = _render_duplicates(())
    assert "no duplicates" in output


def test_render_duplicates_shows_group_paths():
    output = _render_duplicates(
        (DuplicateGroup(digest="abc123", size_bytes=10, paths=("a.txt", "b.txt")),)
    )
    assert "abc123" in output
    assert "a.txt" in output
    assert "b.txt" in output


def test_render_duplicates_escapes_markup_injection_in_path():
    output = _render_duplicates(
        (
            DuplicateGroup(
                digest="abc123", size_bytes=10, paths=("[bold]evil[/bold].txt",)
            ),
        )
    )
    assert "[bold]evil[/bold].txt" in output


def _render_stat(result: StatResult) -> str:
    console = Console(no_color=True, width=200, record=True)
    render_stat(result, console=console)
    return console.export_text()


def test_render_stat_basic():
    output = _render_stat(
        StatResult(
            path="config.json",
            size_bytes=42,
            modified_at="2026-01-01T00:00:00+00:00",
            permissions="644",
            owner="vscode",
            is_symlink=False,
        )
    )
    assert "config.json" in output
    assert "size=42" in output
    assert "perms=644" in output
    assert "owner=vscode" in output


def test_render_stat_windows_none_fields_show_dash():
    output = _render_stat(
        StatResult(
            path="config.json",
            size_bytes=42,
            modified_at="2026-01-01T00:00:00+00:00",
            permissions=None,
            owner=None,
            is_symlink=False,
        )
    )
    assert "—" in output


def test_render_stat_symlink_shows_target():
    output = _render_stat(
        StatResult(
            path="link.json",
            size_bytes=0,
            modified_at="2026-01-01T00:00:00+00:00",
            permissions="777",
            owner="vscode",
            is_symlink=True,
            symlink_target="/real/target.json",
        )
    )
    assert "/real/target.json" in output


def test_render_stat_escapes_markup_injection_in_path():
    output = _render_stat(
        StatResult(
            path="[bold]evil[/bold].json",
            size_bytes=1,
            modified_at="2026-01-01T00:00:00+00:00",
            permissions="644",
            owner="vscode",
            is_symlink=False,
        )
    )
    assert "[bold]evil[/bold].json" in output
