from __future__ import annotations

import hashlib

import pytest
from conftest import clone, mangle_whitespace, reverse_keys

from tool_sentry.adapters import load_tools
from tool_sentry.snapshot import (
    LOCK_VERSION,
    build_snapshot,
    contract_hash,
    hash_bytes,
)


def hash_of(document) -> str:
    return contract_hash(load_tools(document))


def edit_openai(document, index, mutate):
    """Return a copy of an OpenAI document with one tool body mutated."""
    copied = clone(document)
    mutate(copied[index]["function"])
    return copied


# --- equivalence -----------------------------------------------------------


def test_shuffled_key_order_hashes_identically(openai_doc):
    assert hash_of(reverse_keys(clone(openai_doc))) == hash_of(openai_doc)


def test_description_whitespace_variants_hash_identically(openai_doc):
    assert hash_of(mangle_whitespace(clone(openai_doc))) == hash_of(openai_doc)


def test_openai_wrapper_hashes_like_bare_list(openai_doc):
    assert hash_of({"tools": clone(openai_doc)}) == hash_of(openai_doc)


def test_all_three_dialects_agree(openai_doc, anthropic_doc, mcp_doc):
    expected = hash_of(openai_doc)
    assert hash_of(anthropic_doc) == expected
    assert hash_of(mcp_doc) == expected


def test_snapshot_is_deterministic(openai_doc):
    assert build_snapshot(load_tools(openai_doc)) == build_snapshot(
        load_tools(clone(openai_doc))
    )


# --- meaningful differences ------------------------------------------------


def rename_tool(function):
    function["name"] = "search_docs"


def rename_property(function):
    properties = function["parameters"]["properties"]
    properties["q"] = properties.pop("query")


def change_type(function):
    function["parameters"]["properties"]["limit"]["type"] = "string"


def add_required(function):
    function["parameters"]["required"].append("order")


def drop_required(function):
    function["parameters"]["required"].remove("limit")


def reorder_required(function):
    function["parameters"]["required"].reverse()


def reorder_enum(function):
    function["parameters"]["properties"]["order"]["enum"].reverse()


def change_description(function):
    function["description"] = "Search the archive."


def add_constraint(function):
    function["parameters"]["properties"]["limit"]["maximum"] = 100


@pytest.mark.parametrize(
    "mutate",
    [
        rename_tool,
        rename_property,
        change_type,
        add_required,
        drop_required,
        reorder_required,
        reorder_enum,
        change_description,
        add_constraint,
    ],
    ids=lambda fn: fn.__name__,
)
def test_meaningful_change_changes_hash(openai_doc, mutate):
    assert hash_of(edit_openai(openai_doc, 0, mutate)) != hash_of(openai_doc)


def test_tool_roster_order_changes_hash(openai_doc):
    assert hash_of(list(reversed(clone(openai_doc)))) != hash_of(openai_doc)


def test_dropping_a_tool_changes_hash(openai_doc):
    assert hash_of(clone(openai_doc)[:1]) != hash_of(openai_doc)


# --- payload shape ---------------------------------------------------------


def test_snapshot_shape_and_hash_algorithm(openai_doc):
    tools = load_tools(openai_doc)
    snapshot = build_snapshot(tools)
    assert snapshot["version"] == LOCK_VERSION
    assert list(snapshot) == ["version", "hash", "tools"]
    assert snapshot["hash"] == "sha256:" + hashlib.sha256(hash_bytes(tools)).hexdigest()
    assert len(snapshot["hash"]) == len("sha256:") + 64


def test_canonical_tools_omit_source_and_sort_keys(openai_doc):
    snapshot = build_snapshot(load_tools(openai_doc))
    first = snapshot["tools"][0]
    assert list(first) == ["description", "name", "parameters"]
    assert first["description"] == "Search the indexed document corpus. Returns ranked matches."
    assert first["parameters"]["required"] == ["query", "limit"]
    assert first["parameters"]["properties"]["order"]["enum"] == [
        "relevance",
        "recency",
        "title",
    ]


def test_hash_bytes_are_compact_utf8(openai_doc):
    raw = hash_bytes(load_tools(openai_doc))
    assert raw.startswith(b'[{"description":')
    assert b", " not in raw
