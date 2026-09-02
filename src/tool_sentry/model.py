"""Internal representation (IR) for tool contracts.

tool-sentry loads OpenAI function tools, Anthropic tools, and MCP
``tools/list`` payloads into one shape so that contracts expressed in
different provider dialects can be compared directly.

The IR
------
A loaded document is a ``list[Tool]``. Each :class:`Tool` is::

    {
      "name": str,           # tool name, verbatim
      "description": str,    # whitespace-canonicalized
      "parameters": dict,    # JSON Schema object
      "output": dict | None, # JSON Schema object, when the source has one
      "source": str,         # "openai" | "anthropic" | "mcp" | "lock"
    }

``parameters`` comes from ``function.parameters`` (OpenAI),
``input_schema`` (Anthropic), or ``inputSchema`` (MCP). The three names
describe the same thing: the JSON Schema for the tool's arguments.

``output`` comes from ``outputSchema`` (MCP), or from ``output_schema`` /
``output`` when the document already carries one. Most OpenAI and Anthropic
tool documents have no output schema, so it is optional.

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
form that gets hashed is ``{"name", "description", "parameters"}``, plus
``"output"`` when the tool has an output schema, so the same contract
published as an OpenAI function, an Anthropic tool, and an MCP tool produces
one identical hash.

``output`` is omitted from the canonical form when it is absent, so a tool
document without output schemas hashes exactly as it did before output
schemas were supported.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Source = Literal["openai", "anthropic", "mcp", "lock"]

#: Keys always present in the canonical (hashed) form of a tool, sorted.
CANONICAL_KEYS = ("description", "name", "parameters")

#: Canonical keys that appear only when the tool carries that field.
OPTIONAL_CANONICAL_KEYS = ("output",)


@dataclass(frozen=True)
class Tool:
    """One tool contract in the internal representation."""

    name: str
    description: str
    parameters: dict[str, Any]
    source: Source
    output: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the full IR mapping, including ``source``."""
        mapping = {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "source": self.source,
        }
        if self.output is not None:
            mapping["output"] = self.output
        return mapping

    def to_canonical_dict(self) -> dict[str, Any]:
        """Return the contract-bearing mapping that the hash covers.

        ``source`` is excluded on purpose: the same contract expressed in a
        different provider dialect must hash identically. ``output`` is
        included only when present, so tools without one keep their hash.
        """
        mapping = {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }
        if self.output is not None:
            mapping["output"] = self.output
        return mapping
