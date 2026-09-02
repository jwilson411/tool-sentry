"""Local, git-reviewable review policy for classified contract changes.

A policy file says which changes matter. It is a small documented YAML
subset — parsed here, without a YAML library, so tool-sentry keeps zero
runtime dependencies — or the same keys written as JSON::

    version: 1
    fail_on: breaking
    ignore_paths:
      - parameters.properties.experimental
    forbidden_tool_names:
      - "eval"
      - "shell_*"
    tools:
      search_documents:
        fail_on: informational
        ignore_paths:
          - parameters.properties.offset

Everything is read from a file on disk. No policy service is contacted and
nothing is executed.

The supported YAML is block mappings, block sequences of scalars, ``#``
comments, quoted and unquoted scalars, and the empty flow collections ``[]``
and ``{}``. Anchors, aliases, tags, merge keys, block scalars, and multi
document streams are rejected with one error rather than being half-read.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Iterable

from .classify import SEVERITY_RANK, Change
from .model import Tool

#: ``fail_on`` values, shared with ``diff --fail-on``. ``never`` never fails.
FAIL_ON_CHOICES = ("breaking", "risky", "informational", "never")

#: The only policy format version this release understands.
POLICY_VERSION = 1

#: Top-level policy keys.
POLICY_KEYS = ("version", "fail_on", "ignore_paths", "forbidden_tool_names", "tools")

#: Keys allowed inside a per-tool override.
TOOL_POLICY_KEYS = ("fail_on", "ignore_paths")

_INT = re.compile(r"[+-]?[0-9]+$")

_UNSUPPORTED_STARTS = "&*!|>%@`"

_UNSUPPORTED = (
    "unsupported YAML feature on line {line}: anchors, aliases, tags, merge "
    "keys, block scalars, and multi-document streams are not supported; use "
    "plain mappings, sequences, and scalars"
)


class PolicyError(ValueError):
    """Raised for a policy file that cannot be read or does not validate."""


@dataclass(frozen=True)
class ToolPolicy:
    """Per-tool overrides. ``fail_on`` of ``None`` means "use the global"."""

    fail_on: str | None = None
    ignore_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class Policy:
    """A validated review policy."""

    path: str = ""
    fail_on: str = "breaking"
    ignore_paths: tuple[str, ...] = ()
    forbidden_tool_names: tuple[str, ...] = ()
    tools: dict[str, ToolPolicy] = field(default_factory=dict)

    def threshold_for(self, tool: str) -> str:
        """Return the ``fail_on`` that applies to changes on ``tool``."""
        override = self.tools.get(tool)
        if override is not None and override.fail_on is not None:
            return override.fail_on
        return self.fail_on

    def ignores_for(self, tool: str) -> tuple[str, ...]:
        """Return the ignore paths that apply to changes on ``tool``."""
        override = self.tools.get(tool)
        if override is None:
            return self.ignore_paths
        return self.ignore_paths + override.ignore_paths

    def to_dict(self) -> dict[str, Any]:
        """Return the summary embedded in the JSON check report."""
        return {"fail_on": self.fail_on, "path": self.path}


@dataclass(frozen=True)
class Forbidden:
    """One candidate tool name matched by a forbidden-name pattern."""

    pattern: str
    name: str

    def to_dict(self) -> dict[str, str]:
        return {"pattern": self.pattern, "name": self.name}


@dataclass(frozen=True)
class PolicyResult:
    """The outcome of applying a policy to a classified diff."""

    changes: list[Change]
    ignored_count: int
    forbidden: list[Forbidden]
    failed: bool


# --- YAML subset -----------------------------------------------------------


@dataclass(frozen=True)
class _Line:
    number: int
    indent: int
    text: str


def _scan(text: str) -> list[_Line]:
    """Strip blanks and comments, and reject YAML this parser does not do."""
    lines: list[_Line] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if "\t" in raw[:indent]:
            raise PolicyError(f"tabs are not allowed for indentation (line {number})")
        if stripped in ("---", "...") or stripped.startswith("<<:"):
            raise PolicyError(_UNSUPPORTED.format(line=number))
        lines.append(_Line(number, indent, stripped))
    return lines


def _split_key(line: _Line) -> tuple[str, str]:
    """Split ``key: value`` into its key and its (possibly empty) value."""
    text = line.text
    if text.startswith(("'", '"')):
        key, rest = _quoted(text, line)
        if not rest.startswith(":"):
            raise PolicyError(f"expected ':' after the key on line {line.number}")
        return key, rest[1:].strip()
    key, separator, value = text.partition(":")
    if not separator:
        raise PolicyError(
            f"expected 'key: value' on line {line.number}, found {text!r}"
        )
    return key.strip(), value.strip()


def _quoted(text: str, line: _Line) -> tuple[str, str]:
    """Read one quoted scalar, returning it and the remaining text."""
    quote = text[0]
    index = 1
    out: list[str] = []
    while index < len(text):
        char = text[index]
        if char == "\\" and quote == '"' and index + 1 < len(text):
            out.append(text[index + 1])
            index += 2
            continue
        if char == quote:
            return "".join(out), text[index + 1 :].strip()
        out.append(char)
        index += 1
    raise PolicyError(f"unterminated quoted string on line {line.number}")


def _scalar(text: str, line: _Line) -> Any:
    """Parse one scalar value."""
    if not text:
        return None
    if text[0] in _UNSUPPORTED_STARTS:
        raise PolicyError(_UNSUPPORTED.format(line=line.number))
    if text[0] in ("'", '"'):
        value, rest = _quoted(text, line)
        if rest and not rest.startswith("#"):
            raise PolicyError(f"unexpected text after a quoted value on line {line.number}")
        return value
    head = text.split(" #", 1)[0].strip()
    if head in ("[]", "{}"):
        return [] if head == "[]" else {}
    if head[0] in "[{":
        raise PolicyError(
            f"inline collections are not supported on line {line.number}; "
            "use a block sequence or mapping"
        )
    if head in ("null", "~"):
        return None
    if head in ("true", "false"):
        return head == "true"
    if _INT.match(head):
        return int(head)
    return head


def _parse_block(lines: list[_Line], index: int, indent: int) -> tuple[Any, int]:
    if lines[index].text.startswith("-"):
        return _parse_sequence(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _parse_sequence(lines: list[_Line], index: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    while index < len(lines) and lines[index].indent == indent:
        line = lines[index]
        if not line.text.startswith("-"):
            raise PolicyError(
                f"expected a '- ' sequence item on line {line.number}, found {line.text!r}"
            )
        body = line.text[1:].strip()
        if not body:
            raise PolicyError(f"expected a value after '-' on line {line.number}")
        items.append(_scalar(body, line))
        index += 1
    return items, index


def _parse_mapping(lines: list[_Line], index: int, indent: int) -> tuple[dict[str, Any], int]:
    mapping: dict[str, Any] = {}
    while index < len(lines) and lines[index].indent == indent:
        line = lines[index]
        key, value = _split_key(line)
        if not key:
            raise PolicyError(f"empty key on line {line.number}")
        if key in mapping:
            raise PolicyError(f"duplicate key {key!r} on line {line.number}")
        index += 1
        if value and not value.startswith("#"):
            mapping[key] = _scalar(value, line)
            continue
        if index < len(lines) and lines[index].indent > indent:
            mapping[key], index = _parse_block(lines, index, lines[index].indent)
            continue
        mapping[key] = None
    if index < len(lines) and lines[index].indent > indent:
        raise PolicyError(f"inconsistent indentation on line {lines[index].number}")
    return mapping, index


def _parse_yaml(text: str) -> Any:
    """Parse the documented YAML subset. Raises :class:`PolicyError`."""
    lines = _scan(text)
    if not lines:
        return None
    value, index = _parse_block(lines, 0, lines[0].indent)
    if index < len(lines):
        raise PolicyError(f"inconsistent indentation on line {lines[index].number}")
    return value


# --- validation ------------------------------------------------------------


def _string_list(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise PolicyError(f"{where} must be a list of non-empty strings")
    return tuple(value)


def _fail_on(value: Any, where: str) -> str:
    if not isinstance(value, str) or value not in FAIL_ON_CHOICES:
        raise PolicyError(f"{where} must be one of {', '.join(FAIL_ON_CHOICES)}")
    return value


def _check_version(data: dict[str, Any]) -> None:
    if "version" not in data:
        raise PolicyError(
            f"policy is missing the required key 'version' (expected {POLICY_VERSION})"
        )
    version = data["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != POLICY_VERSION:
        raise PolicyError(
            f"stale policy format: version {version!r} is not supported, "
            f"expected version {POLICY_VERSION}"
        )


def _unknown_keys(data: dict[str, Any]) -> list[str]:
    unknown = [key for key in data if key not in POLICY_KEYS]
    tools = data.get("tools")
    if isinstance(tools, dict):
        for name, override in tools.items():
            if isinstance(override, dict):
                unknown.extend(
                    f"tools.{name}.{key}"
                    for key in override
                    if key not in TOOL_POLICY_KEYS
                )
    return sorted(unknown)


def _tool_policies(value: Any) -> dict[str, ToolPolicy]:
    if not isinstance(value, dict):
        raise PolicyError("'tools' must be a mapping of tool name to overrides")
    overrides: dict[str, ToolPolicy] = {}
    for name, body in value.items():
        if not isinstance(body, dict):
            raise PolicyError(f"'tools.{name}' must be a mapping of override keys")
        fail_on = body.get("fail_on")
        overrides[name] = ToolPolicy(
            fail_on=None if fail_on is None else _fail_on(fail_on, f"'tools.{name}.fail_on'"),
            ignore_paths=(
                ()
                if body.get("ignore_paths") is None
                else _string_list(body["ignore_paths"], f"'tools.{name}.ignore_paths'")
            ),
        )
    return overrides


def build_policy(data: Any, path: str = "") -> Policy:
    """Validate a parsed policy document and return a :class:`Policy`."""
    if not isinstance(data, dict):
        raise PolicyError("policy must be a mapping of policy keys")
    _check_version(data)
    unknown = _unknown_keys(data)
    if unknown:
        raise PolicyError(
            f"unknown policy keys: {', '.join(unknown)}; "
            f"known keys are {', '.join(POLICY_KEYS)}"
        )
    return Policy(
        path=path,
        fail_on=(
            "breaking" if data.get("fail_on") is None else _fail_on(data["fail_on"], "'fail_on'")
        ),
        ignore_paths=(
            ()
            if data.get("ignore_paths") is None
            else _string_list(data["ignore_paths"], "'ignore_paths'")
        ),
        forbidden_tool_names=(
            ()
            if data.get("forbidden_tool_names") is None
            else _string_list(data["forbidden_tool_names"], "'forbidden_tool_names'")
        ),
        tools={} if data.get("tools") is None else _tool_policies(data["tools"]),
    )


def load_policy(path: Path) -> Policy:
    """Read and validate the policy file at ``path``.

    JSON is tried first; anything else is read as the documented YAML subset.
    Every failure raises one :class:`PolicyError`.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyError(f"cannot read policy {path}: {exc}") from exc
    if not text.strip():
        raise PolicyError(f"policy {path} is empty")
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError:
        try:
            data = _parse_yaml(text)
        except PolicyError as exc:
            raise PolicyError(f"{path}: {exc}") from exc
    try:
        return build_policy(data, str(path))
    except PolicyError as exc:
        raise PolicyError(f"{path}: {exc}") from exc


# --- evaluation ------------------------------------------------------------


def path_ignored(change_path: str, ignores: Iterable[str]) -> bool:
    """True when ``change_path`` is one of ``ignores`` or sits under one.

    An empty change path — roster-level changes carry one — is never ignored.
    """
    if not change_path:
        return False
    return any(
        change_path == ignore or change_path.startswith(f"{ignore}.")
        for ignore in ignores
        if ignore
    )


def apply_ignores(policy: Policy, changes: list[Change]) -> list[Change]:
    """Return the changes the policy does not ignore, in the same order."""
    return [
        change
        for change in changes
        if not path_ignored(change.path, policy.ignores_for(change.tool))
    ]


def forbidden_names(policy: Policy, candidate: list[Tool]) -> list[Forbidden]:
    """Return every candidate tool name matched by a forbidden pattern."""
    return [
        Forbidden(pattern, tool.name)
        for tool in candidate
        for pattern in policy.forbidden_tool_names
        if fnmatchcase(tool.name, pattern)
    ]


def reaches_threshold(policy: Policy, changes: list[Change]) -> bool:
    """True when any change is at least as severe as its tool's threshold."""
    for change in changes:
        threshold = policy.threshold_for(change.tool)
        if threshold == "never":
            continue
        if SEVERITY_RANK[change.severity] >= SEVERITY_RANK[threshold]:
            return True
    return False


def evaluate(policy: Policy, changes: list[Change], candidate: list[Tool]) -> PolicyResult:
    """Apply ``policy`` to a classified diff and its candidate roster."""
    remaining = apply_ignores(policy, changes)
    forbidden = forbidden_names(policy, candidate)
    return PolicyResult(
        changes=remaining,
        ignored_count=len(changes) - len(remaining),
        forbidden=forbidden,
        failed=bool(forbidden) or reaches_threshold(policy, remaining),
    )


def duplicate_tool_names(tools: list[Tool]) -> list[str]:
    """Return the names that appear more than once, in first-seen order."""
    seen: dict[str, int] = {}
    for tool in tools:
        seen[tool.name] = seen.get(tool.name, 0) + 1
    return [name for name, count in seen.items() if count > 1]
