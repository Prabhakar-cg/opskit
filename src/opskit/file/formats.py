"""Structured-format load/dump adapter: JSON, YAML, TOML, XML behind one interface.

JSON via stdlib ``json``; YAML via PyYAML's ``safe_load``/``safe_dump`` only (never the full
loader — research R1); TOML via ``tomllib``/``tomli`` (read) + ``tomli_w`` (write) — research
R2; XML via ``defusedxml.ElementTree`` (read, XXE-safe) + stdlib ``ElementTree`` (build/write,
no parsing of untrusted input) + :mod:`opskit.file.xml_convert` — research R3.

Every load/dump also reports whether the round-trip is lossless (FR-018): JSON object keys
that collapse duplicates, non-string map keys stringified for JSON output, date/time values
serialized as strings for JSON output, and XML mixed content are all flagged rather than
silently accepted.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import BinaryIO, NamedTuple, cast
from xml.etree.ElementTree import ParseError as XmlParseError
from xml.etree.ElementTree import indent as xml_indent
from xml.etree.ElementTree import tostring as xml_tostring

import defusedxml.ElementTree as DefusedET
import tomli_w
import yaml
from defusedxml.common import DefusedXmlException

from opskit.file import xml_convert
from opskit.file.errors import (
    FileError,
    FileNotFoundOnDisk,
    FilePermissionDenied,
    InvalidContent,
)
from opskit.file.models import StructuredFormat

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

EXTENSION_FORMATS: dict[str, StructuredFormat] = {
    ".json": StructuredFormat.JSON,
    ".yaml": StructuredFormat.YAML,
    ".yml": StructuredFormat.YAML,
    ".toml": StructuredFormat.TOML,
    ".xml": StructuredFormat.XML,
}


class LoadResult(NamedTuple):
    """The outcome of :func:`load`."""

    data: object
    format: StructuredFormat
    lossless: bool
    lossy_reason: str | None


class DumpResult(NamedTuple):
    """The outcome of :func:`dump`."""

    content: bytes
    lossless: bool
    lossy_reason: str | None


def detect_from_extension(path: Path) -> StructuredFormat | None:
    """Best-guess format from a file's extension, or ``None`` if unrecognized."""
    return EXTENSION_FORMATS.get(path.suffix.lower())


def detect_from_content(text: str) -> StructuredFormat | None:
    """Best-guess format by attempting each parser in a deliberate order.

    XML is checked first (an explicit, unambiguous leading marker); JSON before TOML before
    YAML, since YAML's grammar is permissive enough to "succeed" on many non-YAML documents.
    """
    stripped = text.lstrip()
    if stripped.startswith("<"):
        return StructuredFormat.XML
    try:
        json.loads(text)
    except ValueError:
        pass
    else:
        return StructuredFormat.JSON
    try:
        tomllib.loads(text)
    except (tomllib.TOMLDecodeError, TypeError):
        pass
    else:
        return StructuredFormat.TOML
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError:
        pass
    else:
        if isinstance(value, (dict, list)):
            return StructuredFormat.YAML
    return None


def read_bytes(path: Path) -> bytes:
    """Read ``path``'s raw bytes, normalizing OS errors into the typed hierarchy.

    Shared by every command in this category that needs a file's raw content, not just
    structured-format loading.
    """
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise FileNotFoundOnDisk(
            f"file not found: {path}", hint="check the path and try again"
        ) from exc
    except IsADirectoryError as exc:
        raise FileNotFoundOnDisk(
            f"not a file: {path}", hint="pass a file path, not a directory"
        ) from exc
    except PermissionError as exc:
        raise FilePermissionDenied(f"permission denied reading {path}") from exc
    except OSError as exc:
        raise FileError(f"cannot read {path}: {exc}") from exc


def open_binary(path: Path) -> BinaryIO:
    """Open ``path`` for streaming binary reads, normalizing OS errors into the typed hierarchy.

    Shared by every command that scans a file in chunks rather than loading it whole
    (:func:`read_bytes` covers the whole-file case).
    """
    try:
        return path.open("rb")
    except FileNotFoundError as exc:
        raise FileNotFoundOnDisk(
            f"file not found: {path}", hint="check the path and try again"
        ) from exc
    except IsADirectoryError as exc:
        raise FileNotFoundOnDisk(
            f"not a file: {path}", hint="pass a file path, not a directory"
        ) from exc
    except PermissionError as exc:
        raise FilePermissionDenied(f"permission denied reading {path}") from exc
    except OSError as exc:
        raise FileError(f"cannot read {path}: {exc}") from exc


def _decode_text(raw: bytes, path: Path) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InvalidContent(
            f"{path} is not valid UTF-8 text (binary file?)",
            hint="use `opskit file encoding` to inspect its actual encoding",
        ) from exc


def _load_json(text: str, path: Path) -> tuple[object, bool, str | None]:
    seen_duplicate = False

    def _pairs_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        nonlocal seen_duplicate
        keys = [key for key, _ in pairs]
        if len(keys) != len(set(keys)):
            seen_duplicate = True
        return dict(pairs)

    try:
        data = json.loads(text, object_pairs_hook=_pairs_hook)
    except json.JSONDecodeError as exc:
        raise InvalidContent(
            f"invalid JSON in {path}: {exc.msg}", line=exc.lineno, column=exc.colno
        ) from exc
    reason = (
        "duplicate object keys collapsed to the last occurrence"
        if seen_duplicate
        else None
    )
    return data, not seen_duplicate, reason


def _load_yaml(text: str, path: Path) -> tuple[object, bool, str | None]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        line = column = None
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            line, column = mark.line + 1, mark.column + 1
        raise InvalidContent(
            f"invalid YAML in {path}: {exc}", line=line, column=column
        ) from exc
    return data, True, None


def _load_toml(text: str, path: Path) -> tuple[object, bool, str | None]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise InvalidContent(f"invalid TOML in {path}: {exc}") from exc
    return data, True, None


def _load_xml(raw: bytes, path: Path) -> tuple[object, bool, str | None]:
    try:
        root = DefusedET.fromstring(raw)
    except DefusedXmlException as exc:
        raise InvalidContent(
            f"XML in {path} uses a disallowed construct: {exc}"
        ) from exc
    except XmlParseError as exc:
        line, offset = exc.position
        raise InvalidContent(
            f"invalid XML in {path}: {exc}", line=line, column=offset + 1
        ) from exc
    data, lossless = xml_convert.xml_to_data(root)
    reason = (
        "XML mixed content has no lossless representation in this mapping"
        if not lossless
        else None
    )
    return data, lossless, reason


def load(path: str | Path, *, format: StructuredFormat | None = None) -> LoadResult:
    """Parse a structured file, auto-detecting its format unless ``format`` is given.

    Raises:
        FileNotFoundOnDisk / FilePermissionDenied: reading the file failed.
        InvalidContent: the content is not valid for the (detected/declared) format, or the
            format could not be determined at all.
    """
    resolved_path = Path(path)
    raw = read_bytes(resolved_path)
    resolved_format = format or detect_from_extension(resolved_path)

    if resolved_format is None:
        text_for_sniff = _decode_text(raw, resolved_path)
        resolved_format = detect_from_content(text_for_sniff)
        if resolved_format is None:
            raise InvalidContent(
                f"cannot determine the format of {resolved_path}",
                hint="pass --format explicitly",
            )

    if resolved_format is StructuredFormat.XML:
        data, lossless, reason = _load_xml(raw, resolved_path)
        return LoadResult(data, resolved_format, lossless, reason)

    text = _decode_text(raw, resolved_path)
    if resolved_format is StructuredFormat.JSON:
        data, lossless, reason = _load_json(text, resolved_path)
    elif resolved_format is StructuredFormat.TOML:
        data, lossless, reason = _load_toml(text, resolved_path)
    else:
        data, lossless, reason = _load_yaml(text, resolved_path)
    return LoadResult(data, resolved_format, lossless, reason)


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return str(value)


def _check_json_safe(data: object) -> tuple[bool, str | None]:
    reasons: set[str] = set()

    def _walk(value: object) -> None:
        if isinstance(value, dict):
            for key, val in cast("dict[object, object]", value).items():
                if not isinstance(key, str):
                    reasons.add("non-string map keys stringified for JSON output")
                _walk(val)
        elif isinstance(value, list):
            for item in cast("list[object]", value):
                _walk(item)
        elif isinstance(value, (datetime, date, time)):
            reasons.add(
                "date/time values serialized as ISO-8601 strings for JSON output"
            )

    _walk(data)
    if not reasons:
        return True, None
    return False, "; ".join(sorted(reasons))


def dump(
    data: object,
    format: StructuredFormat,
    *,
    indent: int = 2,
    sort_keys: bool = False,
) -> DumpResult:
    """Serialize ``data`` to ``format``, reporting whether the result is lossless (FR-018).

    Raises:
        InvalidContent: ``data`` cannot be represented in ``format`` at all (e.g. a non-mapping
            top-level value for TOML, which requires a table).
    """
    if format is StructuredFormat.JSON:
        lossless, reason = _check_json_safe(data)
        text = json.dumps(
            data,
            indent=indent,
            sort_keys=sort_keys,
            ensure_ascii=False,
            default=_json_default,
        )
        return DumpResult(text.encode("utf-8"), lossless, reason)

    if format is StructuredFormat.YAML:
        text = yaml.safe_dump(
            data,
            indent=indent,
            sort_keys=sort_keys,
            allow_unicode=True,
            default_flow_style=False,
        )
        return DumpResult(text.encode("utf-8"), True, None)

    if format is StructuredFormat.TOML:
        if not isinstance(data, dict):
            raise InvalidContent(
                "TOML documents must be a top-level table",
                hint="the source data isn't a mapping — TOML has no bare-array/scalar root",
            )
        try:
            content = tomli_w.dumps(cast("dict[str, object]", data)).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise InvalidContent(
                f"cannot represent this data in TOML: {exc}",
                hint="TOML has no null/None type — remove or replace null values first",
            ) from exc
        return DumpResult(content, True, None)

    # XML
    root = xml_convert.data_to_xml(data, "root")
    xml_indent(root, space=" " * indent)
    content = xml_tostring(root, encoding="utf-8", xml_declaration=True)
    return DumpResult(content, True, None)
