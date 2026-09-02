"""Command line interface for tool-sentry.

Usage::

    tool-sentry snapshot INPUT [--out PATH]
    tool-sentry diff BASELINE CANDIDATE [--fail-on SEVERITY] [--format FORMAT]
    tool-sentry check --baseline PATH --candidate PATH --policy PATH
    tool-sentry approve --baseline PATH --candidate PATH

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
from .policy import (
    FAIL_ON_CHOICES,
    Forbidden,
    Policy,
    PolicyError,
    PolicyResult,
    duplicate_tool_names,
    evaluate,
    load_policy,
)
from .snapshot import (
    LOCK_VERSION,
    build_approval,
    build_snapshot,
    contract_hash,
    write_snapshot,
)

DEFAULT_OUT = "tools.lock.json"
EXIT_OK = 0
EXIT_CHANGES = 1
EXIT_ERROR = 2

#: Table columns, in print order.
TABLE_COLUMNS = ("SEVERITY", "RULE", "TOOL", "PATH", "MESSAGE")

#: Columns of the forbidden-name table ``check`` prints after the diff table.
FORBIDDEN_COLUMNS = ("FORBIDDEN", "PATTERN", "NAME")

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


def _require_lock_version(file: Path, data: object) -> None:
    """Reject a lock file written in a format version this release predates."""
    if not isinstance(data, dict) or "version" not in data:
        return
    version = data["version"]
    if not isinstance(version, bool) and version == LOCK_VERSION:
        return
    raise InputError(
        f"{file}: stale lock format version {version!r}, expected "
        f"{LOCK_VERSION}; regenerate the baseline with 'tool-sentry snapshot'"
    )


def collect_tools(path: Path, *, check_version: bool = False) -> list[Tool]:
    """Load every tool under ``path`` in a deterministic order."""
    tools: list[Tool] = []
    for file in _input_files(path):
        data = _read_json(file)
        if check_version:
            _require_lock_version(file, data)
        try:
            tools.extend(load_tools(data))
        except AdapterError as exc:
            raise InputError(f"{file}: {exc}") from exc
    if not tools:
        raise InputError(f"no tools found in {path}")
    return tools


def reject_duplicates(tools: list[Tool], role: str) -> None:
    """Fail when a roster names the same tool twice.

    ``check`` and ``approve`` compare and record rosters by name, so a
    repeated name has no single meaning and is rejected rather than guessed.
    """
    duplicates = duplicate_tool_names(tools)
    if duplicates:
        raise InputError(
            f"duplicate tool names in {role}: {', '.join(sorted(duplicates))}; "
            "each tool name must appear once"
        )


def _cmd_snapshot(args: argparse.Namespace) -> int:
    tools = collect_tools(Path(args.input))
    snapshot = build_snapshot(tools)
    out = Path(args.out)
    if out.parent != Path("") and not out.parent.exists():
        raise InputError(f"output directory does not exist: {out.parent}")
    write_snapshot(out, snapshot)
    print(snapshot["hash"])
    return EXIT_OK


def _align(rows: list[tuple[str, ...]]) -> str:
    """Render rows, header first, as an aligned, undecorated table."""
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip()
        for row in rows
    )


def render_table(changes: list[Change]) -> str:
    """Render changes as a stable, aligned, undecorated table."""
    if not changes:
        return NO_CHANGES
    rows: list[tuple[str, ...]] = [TABLE_COLUMNS]
    rows.extend(
        (change.severity, change.rule, change.tool, change.path, change.message)
        for change in changes
    )
    return _align(rows)


def render_forbidden(forbidden: list[Forbidden]) -> str:
    """Render forbidden candidate tool names as an aligned table."""
    rows: list[tuple[str, ...]] = [FORBIDDEN_COLUMNS]
    rows.extend(("FORBIDDEN", entry.pattern, entry.name) for entry in forbidden)
    return _align(rows)


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


def render_check_report(
    baseline: list[Tool],
    candidate: list[Tool],
    result: PolicyResult,
    policy: Policy,
) -> str:
    """Render the machine-readable check report."""
    payload = {
        "baseline_hash": contract_hash(baseline),
        "candidate_hash": contract_hash(candidate),
        "counts": count_by_severity(result.changes),
        "ignored_count": result.ignored_count,
        "forbidden": [entry.to_dict() for entry in result.forbidden],
        "policy": policy.to_dict(),
        "changes": [change.to_dict() for change in result.changes],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False)


def _cmd_check(args: argparse.Namespace) -> int:
    policy = load_policy(Path(args.policy))
    baseline = collect_tools(Path(args.baseline), check_version=True)
    candidate = collect_tools(Path(args.candidate))
    reject_duplicates(baseline, "baseline")
    reject_duplicates(candidate, "candidate")

    result = evaluate(policy, classify(baseline, candidate), candidate)
    if args.json or args.format == "json":
        print(render_check_report(baseline, candidate, result, policy))
    else:
        print(render_table(result.changes))
        if result.forbidden:
            print(render_forbidden(result.forbidden))
    return EXIT_CHANGES if result.failed else EXIT_OK


def _cmd_approve(args: argparse.Namespace) -> int:
    baseline = Path(args.baseline)
    if baseline.is_dir():
        raise InputError(f"baseline must be a lock file, not a directory: {baseline}")
    candidate = collect_tools(Path(args.candidate))
    reject_duplicates(candidate, "candidate")
    if baseline.exists():
        reject_duplicates(collect_tools(baseline, check_version=True), "baseline")
    elif baseline.parent != Path("") and not baseline.parent.exists():
        raise InputError(f"output directory does not exist: {baseline.parent}")

    approval = build_approval(candidate, __version__)
    write_snapshot(baseline, approval)
    print(approval["hash"])
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tool-sentry",
        description=(
            "Canonicalize OpenAI, Anthropic, and MCP tool schemas into a "
            "deterministic lock file with a SHA-256 contract hash, classify "
            "every change against a baseline, and check those changes against "
            "a local review policy. Never executes a model or a tool."
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

    check = subparsers.add_parser(
        "check",
        help="compare a candidate against an approved baseline under a policy",
    )
    check.add_argument(
        "--baseline", required=True, help="approved baseline lock file (or JSON/directory)"
    )
    check.add_argument(
        "--candidate", required=True, help="lock file, JSON file, or directory"
    )
    check.add_argument("--policy", required=True, help="policy file (YAML or JSON)")
    check.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="output format (default: table)",
    )
    check.add_argument("--json", action="store_true", help="alias for --format json")
    check.set_defaults(func=_cmd_check)

    approve = subparsers.add_parser(
        "approve",
        help="write the candidate contract to the baseline as approved",
    )
    approve.add_argument(
        "--baseline", required=True, help="baseline lock file to write (created if absent)"
    )
    approve.add_argument(
        "--candidate", required=True, help="lock file, JSON file, or directory"
    )
    approve.set_defaults(func=_cmd_approve)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI. Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (InputError, AdapterError, PolicyError) as exc:
        print(f"tool-sentry: error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
