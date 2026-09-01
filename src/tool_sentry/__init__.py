"""tool-sentry: canonical snapshots and contract hashes for tool schemas.

Loads OpenAI function tools, Anthropic tools, and MCP ``tools/list`` JSON
into one internal representation, canonicalizes it, and emits a
deterministic lock file with a SHA-256 contract hash.

tool-sentry never executes a model or a tool, and never calls provider APIs.
"""

from __future__ import annotations

from .adapters import (
    AdapterError,
    detect_format,
    load_anthropic,
    load_mcp,
    load_openai,
    load_tools,
)
from .canonicalize import (
    canonical_json,
    canonical_payload,
    canonicalize_schema,
    canonicalize_tools,
    collapse_whitespace,
)
from .model import Tool
from .snapshot import LOCK_VERSION, build_snapshot, contract_hash, write_snapshot

__version__ = "0.1.0"

__all__ = [
    "AdapterError",
    "LOCK_VERSION",
    "Tool",
    "__version__",
    "build_snapshot",
    "canonical_json",
    "canonical_payload",
    "canonicalize_schema",
    "canonicalize_tools",
    "collapse_whitespace",
    "contract_hash",
    "detect_format",
    "load_anthropic",
    "load_mcp",
    "load_openai",
    "load_tools",
    "write_snapshot",
]
