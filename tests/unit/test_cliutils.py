"""Tests for the category-agnostic cliutils extensions: variadic targets and stdin."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from opskit.core.cliutils import (
    collect_target_list,
    collect_targets,
    read_input_file,
    read_input_source,
)
from opskit.core.errors import UsageError


def test_collect_target_list_positionals_only():
    assert collect_target_list(["a:1", "b:2"], None) == ["a:1", "b:2"]


def test_collect_target_list_first_appearance_order(tmp_path: Path):
    listing = tmp_path / "targets.txt"
    listing.write_text("c:3\nd:4\n", encoding="utf-8")
    assert collect_target_list(["a:1", "b:2"], listing) == [
        "a:1",
        "b:2",
        "c:3",
        "d:4",
    ]


def test_collect_target_list_filters_blanks_and_comments(tmp_path: Path):
    listing = tmp_path / "targets.txt"
    listing.write_text("# fleet\n\n  web1:443  \n# db\ndb:5432\n", encoding="utf-8")
    assert collect_target_list(None, listing) == ["web1:443", "db:5432"]


def test_collect_target_list_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("# comment\nweb1:443\n\ndb:5432\n"))
    assert collect_target_list(None, Path("-")) == ["web1:443", "db:5432"]


def test_collect_target_list_positionals_plus_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("db:5432\n"))
    assert collect_target_list(["a:1"], Path("-")) == ["a:1", "db:5432"]


def test_collect_target_list_empty_is_usage_error():
    with pytest.raises(UsageError):
        collect_target_list(None, None)
    with pytest.raises(UsageError):
        collect_target_list([], None)


def test_read_input_source_regular_file(tmp_path: Path):
    listing = tmp_path / "targets.txt"
    listing.write_text("a:1\n", encoding="utf-8")
    assert read_input_source(listing) == ["a:1"]


def test_read_input_file_missing_is_usage_error(tmp_path: Path):
    with pytest.raises(UsageError):
        read_input_file(tmp_path / "missing.txt")


def test_existing_single_target_helper_unchanged(tmp_path: Path):
    listing = tmp_path / "targets.txt"
    listing.write_text("b:2\n", encoding="utf-8")
    assert collect_targets("a:1", listing) == ["a:1", "b:2"]
    with pytest.raises(UsageError):
        collect_targets(None, None)
