"""Deterministic lockfile emission and contract hashing.

The lock file looks like::

    {
      "version": 1,
      "hash": "sha256:<hex>",
      "tools": [ ...canonical tools... ]
    }

An approved baseline, written by ``tool-sentry approve``, adds one block::

    "approved": {"tool_sentry": "0.3.0", "at": "2026-01-02T03:04:05Z"}

The hash covers only the ``tools`` array, serialized as compact canonical
JSON (``sort_keys=True``, ``separators=(",", ":")``, ``ensure_ascii=False``)
and encoded UTF-8. The wrapper is pretty-printed with two-space indent for
readability; because the wrapper is outside the hashed byte string, that
formatting — and the ``approved`` block — cannot affect the hash.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .canonicalize import canonical_json, canonical_payload
from .model import Tool

LOCK_VERSION = 1

#: How ``approved.at`` is formatted: UTC, second precision, trailing ``Z``.
APPROVED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def hash_bytes(tools: list[Tool]) -> bytes:
    """Return the exact bytes that the contract hash is computed over."""
    return canonical_json(canonical_payload(tools)).encode("utf-8")


def contract_hash(tools: list[Tool]) -> str:
    """Return the ``sha256:<hex>`` contract hash for ``tools``."""
    return "sha256:" + hashlib.sha256(hash_bytes(tools)).hexdigest()


def build_snapshot(tools: list[Tool]) -> dict[str, Any]:
    """Build the lock file payload for ``tools``."""
    return {
        "version": LOCK_VERSION,
        "hash": contract_hash(tools),
        "tools": canonical_payload(tools),
    }


def approved_at() -> str:
    """Return the current UTC time, truncated to the second, as ISO-8601."""
    return datetime.now(timezone.utc).strftime(APPROVED_AT_FORMAT)


def build_approval(
    tools: list[Tool], tool_version: str, at: str | None = None
) -> dict[str, Any]:
    """Build an approved baseline payload for ``tools``.

    The ``approved`` block records only the tool-sentry version and the UTC
    approval time, defaulting to now. No user name, host name, or environment
    is recorded, and the block is outside the hashed byte string.
    """
    snapshot = build_snapshot(tools)
    return {
        "version": snapshot["version"],
        "hash": snapshot["hash"],
        "approved": {"tool_sentry": tool_version, "at": at or approved_at()},
        "tools": snapshot["tools"],
    }


def render_snapshot(snapshot: dict[str, Any]) -> str:
    """Render a lock file payload as its on-disk text (trailing newline)."""
    return json.dumps(snapshot, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def write_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    """Write the lock file to ``path`` with UTF-8 encoding."""
    path.write_text(render_snapshot(snapshot), encoding="utf-8")
