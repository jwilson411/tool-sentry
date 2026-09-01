"""Internal representation (IR) for tool contracts.

tool-sentry loads OpenAI function tools, Anthropic tools, and MCP
``tools/list`` payloads into one shape so that contracts expressed in
different provider dialects can be compared directly.

The IR
------
A loaded document is a ``list[Tool]``. Each :class:`Tool` is::

    {
      "name": str,          # tool name, verbatim
      "description": str,   # whitespace-canonicalized
      "parameters": dict,   # JSON Schema object
      "source": str,        # "openai" | "anthropic" | "mcp"
    }

``parameters`` comes from ``function.parameters`` (OpenAI),
``input_schema`` (Anthropic), or ``inputSchema`` (MCP). The three names
describe the same thing: the JSON Schema for the tool's arguments.

Ordering
--------
Tools keep the order they appear in the input. They are deliberately *not*
sorted by name: roster order is part of the contract, and sorting would hide
drift when a tool is added, removed, or moved.

Object keys inside each tool are sorted lexicographically. Arrays are never
reordered, because JSON Schema arrays such as ``required``, ``enum`` and
``prefixItems`` are semantically ordered.

``source`` and the contract hash
--------------------------------
``source`` is provenance metadata, not part of the contract. The canonical
form that gets hashed is ``{"name", "description", "parameters"}`` only, so
the same contract published as an OpenAI function, an Anthropic tool, and an
MCP tool produces one identical hash.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Source = Literal["openai", "anthropic", "mcp"]

#: Keys of the canonical (hashed) form of a tool, in sorted order.
CANONICAL_KEYS = ("description", "name", "parameters")


@dataclass(frozen=True)
class Tool:
    """One tool contract in the internal representation."""

    name: str
    description: str
    parameters: dict[str, Any]
    source: Source

    def to_dict(self) -> dict[str, Any]:
        """Return the full IR mapping, including ``source``."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "source": self.source,
        }

    def to_canonical_dict(self) -> dict[str, Any]:
        """Return the contract-bearing mapping that the hash covers.

        ``source`` is excluded on purpose: the same contract expressed in a
        different provider dialect must hash identically.
        """
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }
