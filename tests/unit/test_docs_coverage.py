"""Docs-coverage gate (constitution Art. II).

Enumerates every registered CLI command and fails if any lacks inline help text or a matching
entry in its category's README — and checks each category README is linked from the root README.
This is the automated "docs completeness" gate; ruff's ``D`` rules cover API docstrings.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opskit.cli import app

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src" / "opskit"


def _discover() -> list[tuple[str, str, str, Path]]:
    """Return (category, command, help_text, category_readme) for every registered command."""
    rows: list[tuple[str, str, str, Path]] = []
    for group in app.registered_groups:
        category = group.name or ""
        sub = group.typer_instance
        readme = _SRC / category / "README.md"
        for cmd in sub.registered_commands:
            callback = cmd.callback
            name = cmd.name or (callback.__name__.replace("_", "-") if callback else "")
            help_text = cmd.help or (callback.__doc__ if callback else "") or ""
            rows.append((category, name, help_text.strip(), readme))
    return rows


_COMMANDS = _discover()
_IDS = [f"{category}-{name}" for category, name, _, _ in _COMMANDS]


def test_commands_were_discovered():
    """Guard: broken discovery would make the parametrized checks vacuously pass."""
    found = {(category, name) for category, name, _, _ in _COMMANDS}
    assert {("dns", "lookup"), ("dns", "reverse"), ("tls", "check")} <= found


@pytest.mark.parametrize(
    ("category", "name", "help_text", "readme"), _COMMANDS, ids=_IDS
)
def test_command_has_help_text(category, name, help_text, readme):
    """Every command ships inline help / a docstring (Art. II a)."""
    assert help_text, f"`opskit {category} {name}` has no help text or docstring"


@pytest.mark.parametrize(
    ("category", "name", "help_text", "readme"), _COMMANDS, ids=_IDS
)
def test_command_documented_in_category_readme(category, name, help_text, readme):
    """Every command has a matching entry in its category's README (Art. II b)."""
    assert readme.exists(), f"missing docs page for `{category}`: {readme}"
    text = readme.read_text(encoding="utf-8")
    needle = f"opskit {category} {name}"
    assert needle in text, f"`{needle}` is not documented in {readme}"


@pytest.mark.parametrize("category", sorted({c for c, _, _, _ in _COMMANDS}))
def test_category_readme_linked_from_root(category):
    """The root README's Commands table links each category's docs page."""
    root_readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    assert f"src/opskit/{category}/README.md" in root_readme, (
        f"root README does not link src/opskit/{category}/README.md"
    )
