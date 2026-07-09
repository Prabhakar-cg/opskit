"""Category-agnostic CLI plumbing shared by every command group.

Batch-input reading, outcome collection with per-target failure tolerance, aggregate
exit-code derivation, JSON-envelope emission, and the ``--watch`` loop. Imports only
``opskit.core`` — no category models (constitution Art. VII).
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

import typer

from opskit.core.errors import OpskitError, UsageError
from opskit.core.exit_codes import ExitCode, exit_code_for
from opskit.core.output import make_console
from opskit.core.result import to_json

_T = TypeVar("_T")

# (exit code, change-detection signature) returned by a command's single execution.
ActionResult = tuple[ExitCode, str]


def _filter_target_lines(raw: str) -> list[str]:
    """Keep non-blank, non-``#``-comment lines, stripped, in order."""
    targets: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            targets.append(stripped)
    return targets


def read_input_file(path: Path) -> list[str]:
    """Read targets from a file, one per line, ignoring blanks and ``#`` comments."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError(f"cannot read input file: {path}") from exc
    return _filter_target_lines(raw)


def read_input_source(path: Path) -> list[str]:
    """Read targets from ``path``, or from stdin when the path is ``-``.

    Both sources apply the same filtering: one target per line, blanks and ``#``
    comments ignored.
    """
    if str(path) == "-":
        return _filter_target_lines(sys.stdin.read())
    return read_input_file(path)


def collect_targets(positional: str | None, input_file: Path | None) -> list[str]:
    """Combine a positional target with any from ``--input-file`` (positional first)."""
    targets: list[str] = []
    if positional:
        targets.append(positional)
    if input_file is not None:
        targets.extend(read_input_file(input_file))
    if not targets:
        raise UsageError("provide a target argument or --input-file")
    return targets


def collect_target_list(
    positionals: Sequence[str] | None, input_file: Path | None
) -> list[str]:
    """Combine variadic positional targets with ``--input-file``/stdin targets.

    Order is first-appearance: positionals (in the order given), then input-file or
    stdin (``-``) lines.

    Raises:
        UsageError: When no targets were provided by either source.
    """
    targets: list[str] = list(positionals or [])
    if input_file is not None:
        targets.extend(read_input_source(input_file))
    if not targets:
        raise UsageError("provide at least one target argument or --input-file")
    return targets


def collect_outcomes(
    targets: Sequence[str], run_one: Callable[[str], _T]
) -> list[tuple[str, _T | None, OpskitError | None]]:
    """Run ``run_one`` for each target, capturing its result or the raised OpskitError."""
    outcomes: list[tuple[str, _T | None, OpskitError | None]] = []
    for target in targets:
        try:
            outcomes.append((target, run_one(target), None))
        except OpskitError as exc:
            outcomes.append((target, None, exc))
    return outcomes


def echo_failures(
    outcomes: Sequence[tuple[str, object, OpskitError | None]],
) -> None:
    """Print each failed target's error to stderr (human mode / no-success bail)."""
    for target, _, error in outcomes:
        if error is not None:
            typer.echo(f"error: {target}: {error.message}", err=True)


def aggregate_exit(codes: Sequence[ExitCode]) -> ExitCode:
    """Batch rule (Art. IX): 0 if all succeed; the class code if uniform; else PARTIAL."""
    if all(code is ExitCode.OK for code in codes):
        return ExitCode.OK
    distinct = {code for code in codes if code is not ExitCode.OK}
    if len(distinct) == 1 and all(code is not ExitCode.OK for code in codes):
        return next(iter(distinct))
    return ExitCode.PARTIAL


def aggregate_outcome_exit(
    outcomes: Sequence[tuple[str, object, OpskitError | None]],
) -> ExitCode:
    """:func:`aggregate_exit` over (target, result, error) outcome tuples."""
    return aggregate_exit(
        [
            ExitCode.OK if error is None else exit_code_for(error)
            for _, _, error in outcomes
        ]
    )


def emit_envelopes(envelopes: Sequence[dict[str, Any]], *, jsonl: bool) -> None:
    """Emit JSON envelopes: NDJSON (one per line), a bare object, or an array."""
    if jsonl:
        for envelope in envelopes:
            typer.echo(to_json(envelope, indent=None))
    elif len(envelopes) == 1:
        typer.echo(to_json(envelopes[0]))
    else:
        typer.echo(json.dumps(envelopes, indent=2))


def parse_interval(text: str) -> float:
    """Parse a --watch interval like '5', '5s', '250ms', or '2m' into seconds."""
    value = text.strip().lower()
    try:
        if value.endswith("ms"):
            seconds = float(value[:-2]) / 1000.0
        elif value.endswith("s"):
            seconds = float(value[:-1])
        elif value.endswith("m"):
            seconds = float(value[:-1]) * 60.0
        else:
            seconds = float(value)
    except ValueError as exc:
        raise UsageError(f"invalid --watch interval: {text}") from exc
    if seconds <= 0:
        raise UsageError("--watch interval must be positive")
    return seconds


def watch(
    action: Callable[[], ActionResult],
    *,
    interval: float,
    no_color: bool,
) -> ExitCode:
    """Re-run ``action`` every ``interval`` seconds until interrupted, flagging changes."""
    console = make_console(no_color=no_color)
    previous: str | None = None
    code = ExitCode.OK
    try:
        while True:
            code, signature = action()
            if previous is None:
                status = "initial"
            elif signature != previous:
                status = "changed"
            else:
                status = "no change"
            stamp = datetime.now().strftime("%H:%M:%S")
            console.print(
                f"[dim]-- {stamp} - {status} - next in {interval:g}s (Ctrl-C to stop) --[/dim]"
            )
            previous = signature
            time.sleep(interval)  # looked up at call time so tests can patch it
    except KeyboardInterrupt:
        return code


def run_or_watch(
    action: Callable[[], ActionResult], *, watch_spec: str | None, no_color: bool
) -> None:
    """Run ``action`` once, or repeatedly under --watch; then exit with its code."""
    if watch_spec is not None:
        try:
            interval = parse_interval(watch_spec)
        except UsageError as error:
            typer.echo(f"error: {error.message}", err=True)
            raise typer.Exit(int(ExitCode.USAGE)) from error
        raise typer.Exit(int(watch(action, interval=interval, no_color=no_color)))
    code, _ = action()
    raise typer.Exit(int(code))
