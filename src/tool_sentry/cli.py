"""Command line interface for tool-sentry.

Usage::

    tool-sentry snapshot INPUT [--out PATH]

``INPUT`` is a JSON file, or a directory of ``.json`` files that are read in
sorted filename order. Nothing is executed and no network call is made.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .adapters import AdapterError, load_tools
from .model import Tool
from .snapshot import build_snapshot, write_snapshot

DEFAULT_OUT = "tools.lock.json"
EXIT_OK = 0
EXIT_ERROR = 2


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
