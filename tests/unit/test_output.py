"""Tests for shared rendering: markup escaping and NO_COLOR handling."""

from __future__ import annotations

from opskit.core.output import make_console, render_records
from opskit.dns.models import DnsRecord, RecordType


def test_render_records_escapes_markup():
    # A TXT value that looks like rich markup must render literally, not as styling.
    console = make_console(no_color=True)
    with console.capture() as capture:
        render_records(
            (DnsRecord(RecordType.TXT, "[red]spoof[/red]", 300),), console=console
        )
    assert "[red]spoof[/red]" in capture.get()


def test_make_console_honors_no_color_env(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert make_console().no_color is True


def test_make_console_forces_plain_when_requested(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert make_console(no_color=True).no_color is True
    assert make_console().no_color is False
