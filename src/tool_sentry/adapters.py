"""Fixture-driven loaders for OpenAI, Anthropic, MCP, and lock documents.

These adapters read JSON that you already have on disk. tool-sentry does not
depend on any provider SDK, opens no network connection, and never calls a
model or a tool.
"""

from __future__ import annotations

from typing import Any

from .model import Source, Tool


class AdapterError(ValueError):
    """Raised when a document cannot be read as tool definitions."""


def _as_items(data: Any, wrapper_keys: tuple[str, ...] = ("tools",)) -> list[Any]:
    """Return the tool item list from a bare list or a wrapper object."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in wrapper_keys:
            if isinstance(data.get(key), list):
                return data[key]
        result = data.get("result")
        if isinstance(result, dict):
            return _as_items(result, wrapper_keys)
    raise AdapterError("expected a list of tools or an object with a 'tools' list")


def _require_str(item: dict[str, Any], key: str, *, required: bool = True) -> str:
    value = item.get(key)
    if value is None:
        if required:
            raise AdapterError(f"tool is missing required string field {key!r}")
        return ""
    if not isinstance(value, str):
        raise AdapterError(f"tool field {key!r} must be a string")
    return value


def _require_schema(item: dict[str, Any], key: str) -> dict[str, Any]:
    value = item.get(key)
    if value is None:
        # A tool with no arguments is legal; normalize to an empty object schema.
        return {"type": "object", "properties": {}}
    if not isinstance(value, dict):
        raise AdapterError(f"tool field {key!r} must be a JSON Schema object")
    return value


#: Keys an output JSON Schema may be published under, in lookup order.
OUTPUT_KEYS = ("outputSchema", "output_schema", "output")


def _optional_schema(item: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any] | None:
    """Return the first schema found under ``keys``, or ``None``."""
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        if not isinstance(value, dict):
            raise AdapterError(f"tool field {key!r} must be a JSON Schema object")
        return value
    return None


def _build(item: Any, schema_key: str, source: Source) -> Tool:
    if not isinstance(item, dict):
        raise AdapterError("each tool entry must be an object")
    return Tool(
        name=_require_str(item, "name"),
        description=_require_str(item, "description", required=False),
        parameters=_require_schema(item, schema_key),
        source=source,
        output=_optional_schema(item, OUTPUT_KEYS),
    )


def load_openai(data: Any) -> list[Tool]:
    """Load Chat Completions ``tools`` entries (``type: function``)."""
    tools: list[Tool] = []
    for item in _as_items(data):
        if not isinstance(item, dict):
            raise AdapterError("each tool entry must be an object")
        body = item.get("function", item)
        if not isinstance(body, dict):
            raise AdapterError("'function' must be an object")
        tools.append(_build(body, "parameters", "openai"))
    return tools


def load_anthropic(data: Any) -> list[Tool]:
    """Load Anthropic tools (``name``, ``description``, ``input_schema``)."""
    return [_build(item, "input_schema", "anthropic") for item in _as_items(data)]


def load_mcp(data: Any) -> list[Tool]:
    """Load an MCP ``tools/list`` result (``tools`` of ``inputSchema``)."""
    return [_build(item, "inputSchema", "mcp") for item in _as_items(data)]


def load_lock(data: Any) -> list[Tool]:
    """Load a tool-sentry lock file (``version`` + ``hash`` + ``tools``)."""
    return [_build(item, "parameters", "lock") for item in _as_items(data)]


LOADERS = {
    "openai": load_openai,
    "anthropic": load_anthropic,
    "mcp": load_mcp,
    "lock": load_lock,
}


def is_lock_document(data: Any) -> bool:
    """Return True when ``data`` looks like a tool-sentry lock file."""
    return (
        isinstance(data, dict)
        and "version" in data
        and "hash" in data
        and isinstance(data.get("tools"), list)
    )


def _detect_items(items: list[Any]) -> Source | None:
    objects = [item for item in items if isinstance(item, dict)]
    if not objects:
        return None
    if any(
        item.get("type") == "function" and isinstance(item.get("function"), dict)
        for item in objects
    ):
        return "openai"
    if any("inputSchema" in item for item in objects):
        return "mcp"
    if any("input_schema" in item for item in objects):
        return "anthropic"
    if any("parameters" in item for item in objects):
        return "openai"
    return None


def detect_format(data: Any) -> Source:
    """Detect which dialect ``data`` is written in.

    Raises :class:`AdapterError` if the document is not recognizable.
    """
    if is_lock_document(data):
        return "lock"
    try:
        items = _as_items(data)
    except AdapterError as exc:
        raise AdapterError(f"unrecognized tool document: {exc}") from exc
    detected = _detect_items(items)
    if detected is None:
        raise AdapterError(
            "unrecognized tool document: expected OpenAI function tools "
            "(type/function), Anthropic tools (input_schema), or an MCP "
            "tools/list result (inputSchema)"
        )
    return detected


def load_tools(data: Any, source: Source | None = None) -> list[Tool]:
    """Load ``data`` into the IR, auto-detecting the dialect if needed."""
    resolved = source or detect_format(data)
    return LOADERS[resolved](data)
