"""tool-sentry: canonical snapshots and contract hashes for tool schemas.

Loads OpenAI function tools, Anthropic tools, and MCP ``tools/list`` JSON
into one internal representation, canonicalizes it, and emits a
deterministic lock file with a SHA-256 contract hash. Two contracts can then
be compared with :func:`classify`, which labels every change breaking,
risky, or informational using fixed structural rules, and those changes can
be judged against a local review policy with :func:`load_policy` and
:func:`evaluate`.

tool-sentry never executes a model or a tool, never calls provider APIs, and
never stores secrets.
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
from .policy import (
    FAIL_ON_CHOICES,
    POLICY_VERSION,
    Forbidden,
    Policy,
    PolicyError,
    PolicyResult,
    ToolPolicy,
    apply_ignores,
    build_policy,
    duplicate_tool_names,
    evaluate,
    forbidden_names,
    load_policy,
    path_ignored,
    reaches_threshold,
)
from .snapshot import (
    LOCK_VERSION,
    build_approval,
    build_snapshot,
    contract_hash,
    write_snapshot,
)

__version__ = "0.3.0"

__all__ = [
    "AdapterError",
    "FAIL_ON_CHOICES",
    "LOCK_VERSION",
    "POLICY_VERSION",
    "SEVERITY_ORDER",
    "SEVERITY_RANK",
    "Change",
    "Forbidden",
    "Policy",
    "PolicyError",
    "PolicyResult",
    "Severity",
    "Tool",
    "ToolPolicy",
    "__version__",
    "apply_ignores",
    "build_approval",
    "build_policy",
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
    "duplicate_tool_names",
    "evaluate",
    "forbidden_names",
    "load_anthropic",
    "load_lock",
    "load_mcp",
    "load_openai",
    "load_policy",
    "load_tools",
    "path_ignored",
    "reaches",
    "reaches_threshold",
    "write_snapshot",
]
