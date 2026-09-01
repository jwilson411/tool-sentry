from __future__ import annotations

import pytest
from conftest import clone

from tool_sentry.adapters import (
    AdapterError,
    detect_format,
    load_anthropic,
    load_mcp,
    load_openai,
    load_tools,
)


def test_openai_fixture_loads_into_ir(openai_doc):
    tools = load_openai(openai_doc)
    assert [tool.name for tool in tools] == ["search_documents", "create_ticket"]
    assert all(tool.source == "openai" for tool in tools)
    assert tools[0].parameters["properties"]["query"]["type"] == "string"


def test_anthropic_fixture_loads_into_ir(anthropic_doc):
    tools = load_anthropic(anthropic_doc)
    assert [tool.name for tool in tools] == ["search_documents", "create_ticket"]
    assert all(tool.source == "anthropic" for tool in tools)
    assert tools[0].parameters["required"] == ["query", "limit"]


def test_mcp_fixture_loads_into_ir(mcp_doc):
    tools = load_mcp(mcp_doc)
    assert [tool.name for tool in tools] == ["search_documents", "create_ticket"]
    assert all(tool.source == "mcp" for tool in tools)
    assert tools[1].parameters["properties"]["assignee"]["required"] == ["email"]


def test_tool_order_follows_input_order(openai_doc):
    reversed_doc = list(reversed(clone(openai_doc)))
    assert [tool.name for tool in load_openai(reversed_doc)] == [
        "create_ticket",
        "search_documents",
    ]


def test_detect_format(openai_doc, anthropic_doc, mcp_doc):
    assert detect_format(openai_doc) == "openai"
    assert detect_format(anthropic_doc) == "anthropic"
    assert detect_format(mcp_doc) == "mcp"


def test_detect_openai_wrapper(openai_doc):
    assert detect_format({"tools": clone(openai_doc)}) == "openai"
    assert len(load_tools({"tools": clone(openai_doc)})) == 2


def test_detect_anthropic_wrapper(anthropic_doc):
    assert detect_format({"tools": clone(anthropic_doc)}) == "anthropic"


def test_detect_mcp_jsonrpc_result(mcp_doc):
    envelope = {"jsonrpc": "2.0", "id": 1, "result": clone(mcp_doc)}
    assert detect_format(envelope) == "mcp"
    assert [tool.name for tool in load_tools(envelope)] == [
        "search_documents",
        "create_ticket",
    ]


@pytest.mark.parametrize(
    "document",
    [
        {"messages": [{"role": "user"}]},
        [{"not": "a tool"}],
        [],
        42,
    ],
)
def test_detection_failure_raises(document):
    with pytest.raises(AdapterError):
        detect_format(document)


def test_missing_name_is_an_error():
    with pytest.raises(AdapterError):
        load_anthropic([{"description": "no name", "input_schema": {}}])


def test_missing_schema_defaults_to_empty_object():
    (tool,) = load_anthropic([{"name": "ping", "description": "Ping."}])
    assert tool.parameters == {"type": "object", "properties": {}}


def test_missing_description_defaults_to_empty_string():
    (tool,) = load_mcp({"tools": [{"name": "ping", "inputSchema": {"type": "object"}}]})
    assert tool.description == ""


def test_non_string_description_is_an_error():
    with pytest.raises(AdapterError):
        load_mcp({"tools": [{"name": "ping", "description": 7, "inputSchema": {}}]})
