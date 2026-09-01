"""Deterministic lockfile emission and contract hashing.

The lock file looks like::

    {
      "version": 1,
      "hash": "sha256:<hex>",
      "tools": [ ...canonical tools... ]
    }

The hash covers only the ``tools`` array, serialized as compact canonical
JSON (``sort_keys=True``, ``separators=(",", ":")``, ``ensure_ascii=False``)
and encoded UTF-8. The wrapper is pretty-printed with two-space indent for
readability; because the wrapper is outside the hashed byte string, that
formatting cannot affect the hash.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .canonicalize import canonical_json, canonical_payload
from .model import Tool

LOCK_VERSION = 1


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


def render_snapshot(snapshot: dict[str, Any]) -> str:
    """Render a lock file payload as its on-disk text (trailing newline)."""
    return json.dumps(snapshot, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def write_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    """Write the lock file to ``path`` with UTF-8 encoding."""
    path.write_text(render_snapshot(snapshot), encoding="utf-8")
