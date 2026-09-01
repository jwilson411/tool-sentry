from __future__ import annotations

from tool_sentry.canonicalize import (
    canonical_json,
    canonicalize_schema,
    canonicalize_tools,
    collapse_whitespace,
)
from tool_sentry.model import Tool


def test_collapse_whitespace():
    assert collapse_whitespace("  a\n\n  b\tc  ") == "a b c"


def test_object_keys_are_sorted():
    canonical = canonicalize_schema({"type": "object", "additionalProperties": False})
    assert list(canonical) == ["additionalProperties", "type"]


def test_nested_object_keys_are_sorted():
    canonical = canonicalize_schema(
        {"properties": {"b": {"type": "string"}, "a": {"minimum": 1, "type": "integer"}}}
    )
    assert list(canonical["properties"]) == ["a", "b"]
    assert list(canonical["properties"]["a"]) == ["minimum", "type"]


def test_nested_descriptions_are_collapsed():
    canonical = canonicalize_schema(
        {
            "properties": {
                "q": {"description": "line one\n   line two", "type": "string"}
            }
        }
    )
    assert canonical["properties"]["q"]["description"] == "line one line two"


def test_required_order_is_preserved():
    canonical = canonicalize_schema({"required": ["zeta", "alpha", "mid"]})
    assert canonical["required"] == ["zeta", "alpha", "mid"]


def test_enum_order_is_preserved():
    canonical = canonicalize_schema({"enum": ["c", "a", "b"]})
    assert canonical["enum"] == ["c", "a", "b"]


def test_prefix_items_order_is_preserved():
    canonical = canonicalize_schema(
        {"prefixItems": [{"type": "string"}, {"type": "integer"}]}
    )
    assert [item["type"] for item in canonical["prefixItems"]] == ["string", "integer"]


def test_property_named_description_is_not_treated_as_text():
    canonical = canonicalize_schema(
        {"properties": {"description": {"type": "string", "maxLength": 10}}}
    )
    assert canonical["properties"]["description"] == {
        "maxLength": 10,
        "type": "string",
    }


def test_canonicalize_tools_keeps_order_and_source():
    tools = [
        Tool("b", "  B  tool ", {"type": "object"}, "openai"),
        Tool("a", "A\ntool", {"type": "object"}, "openai"),
    ]
    canonical = canonicalize_tools(tools)
    assert [tool.name for tool in canonical] == ["b", "a"]
    assert [tool.description for tool in canonical] == ["B tool", "A tool"]
    assert canonical[0].source == "openai"


def test_canonical_json_is_compact_and_sorted():
    assert canonical_json({"b": 1, "a": [2, 1]}) == '{"a":[2,1],"b":1}'


def test_canonical_json_keeps_non_ascii():
    assert canonical_json({"a": "café"}) == '{"a":"café"}'
