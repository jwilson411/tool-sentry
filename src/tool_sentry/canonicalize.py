"""Canonicalization of tool contracts.

Rules, in full:

1. Recurse through every dict and list.
2. Sort object keys lexicographically at every object.
3. For description-like fields (:data:`DESCRIPTION_KEYS`) whose value is a
   string, collapse runs of whitespace (including newlines and tabs) to a
   single space and strip the ends. This applies to the tool description and
   to nested JSON Schema ``description`` fields.
4. Never sort arrays. ``required``, ``enum`` and ``prefixItems`` are
   semantically ordered, so reordering them is a real contract change.
5. The hashed byte string is the compact JSON dump of the canonical tools
   array: ``sort_keys=True, separators=(",", ":"), ensure_ascii=False``,
   encoded UTF-8.
"""

from __future__ import annotations

import json
from typing import Any

from .model import Tool

#: Fields whose string values are whitespace-normalized.
DESCRIPTION_KEYS = frozenset({"description", "title"})


def collapse_whitespace(text: str) -> str:
    """Collapse whitespace runs to single spaces and strip the ends."""
    return " ".join(text.split())


def canonicalize_value(value: Any, *, description_like: bool = False) -> Any:
    """Return ``value`` canonicalized recursively.

    ``description_like`` marks values reached through a key in
    :data:`DESCRIPTION_KEYS`; only those strings get whitespace-collapsed.
    """
    if isinstance(value, dict):
        return {
            key: canonicalize_value(
                value[key], description_like=key in DESCRIPTION_KEYS
            )
            for key in sorted(value)
        }
    if isinstance(value, list):
        # Order is preserved: JSON Schema arrays carry meaning.
        return [canonicalize_value(item) for item in value]
    if description_like and isinstance(value, str):
        return collapse_whitespace(value)
    return value


def canonicalize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize a JSON Schema object."""
    return canonicalize_value(schema)


def canonicalize_tool(tool: Tool) -> Tool:
    """Return a copy of ``tool`` with description and schema canonicalized."""
    return Tool(
        name=tool.name,
        description=collapse_whitespace(tool.description),
        parameters=canonicalize_schema(tool.parameters),
        source=tool.source,
    )


def canonicalize_tools(tools: list[Tool]) -> list[Tool]:
    """Canonicalize each tool, keeping input order."""
    return [canonicalize_tool(tool) for tool in tools]


def canonical_payload(tools: list[Tool]) -> list[dict[str, Any]]:
    """Return the canonical, hashable form of ``tools`` (no ``source``).

    Keys are sorted at the tool level too, so the pretty-printed lock file
    reads in the same order as the hashed compact JSON.
    """
    return [canonicalize_value(tool.to_canonical_dict()) for tool in tools]


def canonical_json(value: Any) -> str:
    """Dump ``value`` as compact canonical JSON."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
