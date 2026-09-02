"""Command line interface for tool-sentry.

Usage::

    tool-sentry snapshot INPUT [--out PATH]
    tool-sentry diff BASELINE CANDIDATE [--fail-on SEVERITY] [--format FORMAT]

Every path is a JSON file, or a directory of ``.json`` files that are read in
sorted filename order. Lock files are accepted anywhere a dialect document
is. Nothing is executed and no network call is made.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .adapters import AdapterError, load_tools
from .classify import Change, classify, count_by_severity, reaches
from .model import Tool
from .snapshot import build_snapshot, contract_hash, write_snapshot

DEFAULT_OUT = "tools.lock.json"
EXIT_OK = 0
EXIT_CHANGES = 1
EXIT_ERROR = 2

#: ``--fail-on`` values. ``never`` exits 0 on any successful compare.
FAIL_ON_CHOICES = ("breaking", "risky", "informational", "never")

#: Table columns, in print order.
TABLE_COLUMNS = ("SEVERITY", "RULE", "TOOL", "PATH", "MESSAGE")

NO_CHANGES = "No contract changes."


class InputError(Exception):
    """Raised for unusable CLI input."""


def _read_json(path: Path) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError(f"cannot read {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(f"{path} is not valid JSON: {exc}") from exc


def _input_files(path: Path) -> list[Path]:
    if path.is_dir():
        files = sorted(p for p in path.iterdir() if p.is_file() and p.suffix == ".json")
        if not files:
            raise InputError(f"no .json files found in directory {path}")
        return files
    if path.is_file():
        return [path]
    raise InputError(f"no such file or directory: {path}")


def collect_tools(path: Path) -> list[Tool]:
    """Load every tool under ``path`` in a deterministic order."""
    tools: list[Tool] = []
    for file in _input_files(path):
        data = _read_json(file)
        try:
            tools.extend(load_tools(data))
        except AdapterError as exc:
            raise InputError(f"{file}: {exc}") from exc
    if not tools:
        raise InputError(f"no tools found in {path}")
    return tools


def _cmd_snapshot(args: argparse.Namespace) -> int:
    tools = collect_tools(Path(args.input))
    snapshot = build_snapshot(tools)
    out = Path(args.out)
    if out.parent != Path("") and not out.parent.exists():
        raise InputError(f"output directory does not exist: {out.parent}")
    write_snapshot(out, snapshot)
    print(snapshot["hash"])
    return EXIT_OK


def render_table(changes: list[Change]) -> str:
    """Render changes as a stable, aligned, undecorated table."""
    if not changes:
        return NO_CHANGES
    rows = [TABLE_COLUMNS]
    rows.extend(
        (change.severity, change.rule, change.tool, change.path, change.message)
        for change in changes
    )
    widths = [max(len(row[index]) for row in rows) for index in range(len(TABLE_COLUMNS))]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip()
        for row in rows
    )


def render_report(
    baseline: list[Tool], candidate: list[Tool], changes: list[Change]
) -> str:
    """Render the machine-readable diff report."""
    payload = {
        "baseline_hash": contract_hash(baseline),
        "candidate_hash": contract_hash(candidate),
        "counts": count_by_severity(changes),
        "changes": [change.to_dict() for change in changes],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False)


def _cmd_diff(args: argparse.Namespace) -> int:
    baseline = collect_tools(Path(args.baseline))
    candidate = collect_tools(Path(args.candidate))
    changes = classify(baseline, candidate)
    if args.json or args.format == "json":
        print(render_report(baseline, candidate, changes))
    else:
        print(render_table(changes))
    if args.fail_on != "never" and reaches(changes, args.fail_on):
        return EXIT_CHANGES
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tool-sentry",
        description=(
            "Canonicalize OpenAI, Anthropic, and MCP tool schemas into a "
            "deterministic lock file with a SHA-256 contract hash. "
            "Never executes a model or a tool."
        ),
    )
    parser.add_argument("--version", action="version", version=f"tool-sentry {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser(
        "snapshot",
        help="write a canonical tool lock file and print its contract hash",
    )
    snapshot.add_argument("input", metavar="INPUT", help="JSON file or directory of JSON files")
    snapshot.add_argument(
        "--out",
        default=DEFAULT_OUT,
        help=f"lock file path (default: {DEFAULT_OUT})",
    )
    snapshot.set_defaults(func=_cmd_snapshot)

    diff = subparsers.add_parser(
        "diff",
        help="classify every change between a baseline and a candidate contract",
    )
    diff.add_argument("baseline", metavar="BASELINE", help="lock file, JSON file, or directory")
    diff.add_argument("candidate", metavar="CANDIDATE", help="lock file, JSON file, or directory")
    diff.add_argument(
        "--fail-on",
        choices=FAIL_ON_CHOICES,
        default="breaking",
        help="exit 1 when a change reaches this severity (default: breaking)",
    )
    diff.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="output format (default: table)",
    )
    diff.add_argument(
        "--json",
        action="store_true",
        help="alias for --format json",
    )
    diff.set_defaults(func=_cmd_diff)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI. Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (InputError, AdapterError) as exc:
        print(f"tool-sentry: error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
