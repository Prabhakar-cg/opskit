"""Tests for the storage CLI: envelope shape, exit codes, human output smoke."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from opskit.cli import app
from opskit.core.errors import UsageError
from opskit.storage.errors import PathNotFound
from opskit.storage.models import (
    DirSizeResult,
    Disk,
    InaccessiblePath,
    Partition,
    Volume,
)

runner = CliRunner()


def _volume(mountpoint="/", fstype="ext4", is_network=False):
    return Volume(
        mountpoint=mountpoint,
        device="/dev/sda1",
        fstype=fstype,
        total_bytes=1000,
        used_bytes=400,
        free_bytes=600,
        percent_used=40.0,
        is_network=is_network,
    )


def test_volumes_json_envelope_shape(monkeypatch):
    monkeypatch.setattr("opskit.storage.cli.api.list_volumes", lambda: (_volume(),))
    result = runner.invoke(app, ["storage", "volumes", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "1"
    assert payload["command"] == "storage.volumes"
    assert payload["query"] == {}
    assert payload["result"]["volumes"][0]["mountpoint"] == "/"
    assert payload["error"] is None


def test_volumes_jsonl_one_object_per_volume(monkeypatch):
    monkeypatch.setattr(
        "opskit.storage.cli.api.list_volumes",
        lambda: (_volume(mountpoint="/"), _volume(mountpoint="/data")),
    )
    result = runner.invoke(app, ["storage", "volumes", "--jsonl"])
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert [line["mountpoint"] for line in lines] == ["/", "/data"]


def test_volumes_human_output_smoke(monkeypatch):
    monkeypatch.setattr("opskit.storage.cli.api.list_volumes", lambda: (_volume(),))
    result = runner.invoke(app, ["storage", "volumes", "--no-color"])
    assert result.exit_code == 0
    assert "/" in result.stdout
    assert "ext4" in result.stdout


def test_volumes_empty_list_human_notice(monkeypatch):
    monkeypatch.setattr("opskit.storage.cli.api.list_volumes", lambda: ())
    result = runner.invoke(app, ["storage", "volumes", "--no-color"])
    assert result.exit_code == 0
    assert "No mounted volumes found." in result.stdout


def _disk(disk_id="sda", size_bytes=1000, model="ACME SSD", removable=False):
    return Disk(
        id=disk_id,
        size_bytes=size_bytes,
        model=model,
        removable=removable,
        partitions=(
            Partition(
                device=f"/dev/{disk_id}1",
                size_bytes=900,
                mounted=True,
                mountpoint="/",
                fstype="ext4",
            ),
        ),
    )


def test_disks_json_envelope_shape(monkeypatch):
    monkeypatch.setattr("opskit.storage.cli.api.list_disks", lambda: (_disk(),))
    result = runner.invoke(app, ["storage", "disks", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "storage.disks"
    assert payload["query"] == {}
    disk = payload["result"]["disks"][0]
    assert disk["id"] == "sda"
    assert disk["partitions"][0]["mountpoint"] == "/"
    assert payload["error"] is None


def test_disks_unavailable_fields_are_null_not_omitted(monkeypatch):
    monkeypatch.setattr(
        "opskit.storage.cli.api.list_disks",
        lambda: (_disk(model=None, removable=None),),
    )
    result = runner.invoke(app, ["storage", "disks", "--json"])
    disk = json.loads(result.stdout)["result"]["disks"][0]
    assert "model" in disk
    assert disk["model"] is None
    assert "removable" in disk
    assert disk["removable"] is None


def test_disks_jsonl_one_object_per_disk(monkeypatch):
    monkeypatch.setattr(
        "opskit.storage.cli.api.list_disks", lambda: (_disk("sda"), _disk("sdb"))
    )
    result = runner.invoke(app, ["storage", "disks", "--jsonl"])
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert [line["id"] for line in lines] == ["sda", "sdb"]


def test_disks_human_output_smoke(monkeypatch):
    monkeypatch.setattr("opskit.storage.cli.api.list_disks", lambda: (_disk(),))
    result = runner.invoke(app, ["storage", "disks", "--no-color"])
    assert result.exit_code == 0
    assert "sda" in result.stdout
    assert "ACME SSD" in result.stdout


def _size_result(path="/data", total=100, incomplete=False, depth=0):
    inaccessible = (
        (InaccessiblePath(path=f"{path}/blocked", reason="permission denied"),)
        if incomplete
        else ()
    )
    return DirSizeResult(
        path=path,
        total_bytes=total,
        file_count=3,
        dir_count=1,
        include_hidden=False,
        depth_requested=depth,
        breakdown=(),
        inaccessible=inaccessible,
    )


def test_size_json_envelope_shape(monkeypatch):
    monkeypatch.setattr(
        "opskit.storage.cli.api.dir_size", lambda p, **kw: _size_result(path=p)
    )
    result = runner.invoke(app, ["storage", "size", "/data", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "storage.size"
    assert payload["query"]["path"] == "/data"
    assert payload["result"]["path"] == "/data"
    assert payload["error"] is None


def test_size_not_found_exit_code(monkeypatch):
    def fake(p, **kw):
        raise PathNotFound(f"path does not exist: {p}", hint="check the path and retry")

    monkeypatch.setattr("opskit.storage.cli.api.dir_size", fake)
    result = runner.invoke(app, ["storage", "size", "/no/such/path"])
    assert result.exit_code == 16
    try:
        stderr_text = result.stderr
    except ValueError:  # click < 8.2 mixes the streams into output
        stderr_text = result.output
    assert "path does not exist" in stderr_text


def test_size_incomplete_result_exits_partial(monkeypatch):
    monkeypatch.setattr(
        "opskit.storage.cli.api.dir_size",
        lambda p, **kw: _size_result(path=p, incomplete=True),
    )
    result = runner.invoke(app, ["storage", "size", "/data", "--json"])
    assert result.exit_code == 7
    payload = json.loads(result.stdout)
    assert payload["result"]["incomplete"] is True


def test_size_batch_mixed_outcomes_every_target_in_output(monkeypatch):
    def fake(p, **kw):
        if p == "/missing":
            raise PathNotFound("path does not exist: /missing")
        return _size_result(path=p)

    monkeypatch.setattr("opskit.storage.cli.api.dir_size", fake)
    result = runner.invoke(app, ["storage", "size", "/data", "/missing", "--jsonl"])
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert [line["query"]["path"] for line in lines] == ["/data", "/missing"]
    assert lines[0]["error"] is None
    assert lines[1]["result"] is None
    assert lines[1]["error"]["code"] == "path_not_found"
    assert result.exit_code == 7  # PARTIAL: one succeeded, one failed


def test_size_negative_depth_is_usage_error(monkeypatch):
    def fake(p, **kw):
        if kw.get("depth", 0) < 0:
            raise UsageError("--depth must be >= 0")
        return _size_result(path=p)

    monkeypatch.setattr("opskit.storage.cli.api.dir_size", fake)
    result = runner.invoke(app, ["storage", "size", "/data", "--depth", "-1"])
    assert result.exit_code == 2


def test_size_human_batch_prefixes_target(monkeypatch):
    monkeypatch.setattr(
        "opskit.storage.cli.api.dir_size", lambda p, **kw: _size_result(path=p)
    )
    result = runner.invoke(app, ["storage", "size", "/a", "/b", "--no-color"])
    assert "/a" in result.stdout
    assert "/b" in result.stdout


def test_size_no_targets_is_usage_error():
    result = runner.invoke(app, ["storage", "size"])
    assert result.exit_code == 2
