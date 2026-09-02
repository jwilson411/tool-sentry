"""tool-sentry: canonical snapshots and contract hashes for tool schemas.

Loads OpenAI function tools, Anthropic tools, and MCP ``tools/list`` JSON
into one internal representation, canonicalizes it, and emits a
deterministic lock file with a SHA-256 contract hash. Two contracts can then
be compared with :func:`classify`, which labels every change breaking,
risky, or informational using fixed structural rules.

tool-sentry never executes a model or a tool, and never calls provider APIs.
"""

from __future__ import annotations

from .adapters import (
    AdapterError,
    detect_format,
    load_anthropic,
    load_lock,
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
from .classify import (
    SEVERITY_ORDER,
    SEVERITY_RANK,
    Change,
    Severity,
    classify,
    count_by_severity,
    reaches,
)
from .model import Tool
from .snapshot import LOCK_VERSION, build_snapshot, contract_hash, write_snapshot

__version__ = "0.2.0"

__all__ = [
    "AdapterError",
    "LOCK_VERSION",
    "SEVERITY_ORDER",
    "SEVERITY_RANK",
    "Change",
    "Severity",
    "Tool",
    "__version__",
    "build_snapshot",
    "canonical_json",
    "canonical_payload",
    "canonicalize_schema",
    "canonicalize_tools",
    "classify",
    "collapse_whitespace",
    "contract_hash",
    "count_by_severity",
    "detect_format",
    "load_anthropic",
    "load_lock",
    "load_mcp",
    "load_openai",
    "load_tools",
    "reaches",
    "write_snapshot",
]
