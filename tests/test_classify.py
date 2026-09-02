"""Rule-by-rule tests for the structural change classifier.

Each case states a baseline contract, a candidate contract, and the exact set
of ``(severity, rule, path)`` triples the classifier must produce. Comparing
the full set — not just "contains" — keeps a new rule from silently doubling
up on a path that an existing rule already covers.
"""

from __future__ import annotations

import pytest

from tool_sentry.classify import classify, count_by_severity, reaches
from tool_sentry.model import Tool

DESCRIPTION = "Search the indexed document corpus."


def tool(
    name: str = "search_documents",
    *,
    description: str = DESCRIPTION,
    parameters: dict | None = None,
    output: dict | None = None,
) -> Tool:
    return Tool(
        name=name,
        description=description,
        parameters=parameters if parameters is not None else obj({}),
        source="lock",
        output=output,
    )


def obj(properties: dict, required: list[str] | None = None) -> dict:
    """Build an object schema, omitting ``required`` when it is not given."""
    schema: dict = {"type": "object", "properties": properties}
    if required is not None:
        schema["required"] = required
    return schema


def triples(changes) -> list[tuple[str, str, str]]:
    return [(change.severity, change.rule, change.path) for change in changes]


# --- input schema rules ----------------------------------------------------

# (id, baseline parameters, candidate parameters, expected triples)
SCHEMA_CASES = [
    (
        "identical",
        obj({"query": {"type": "string"}}, ["query"]),
        obj({"query": {"type": "string"}}, ["query"]),
        [],
    ),
    (
        "nested-object-type-incompatible",
        obj({"filter": obj({"tag": {"type": "string"}})}),
        obj({"filter": obj({"tag": {"type": "integer"}})}),
        [("breaking", "type.incompatible", "parameters.properties.filter.properties.tag.type")],
    ),
    (
        "nested-object-optional-added",
        obj({"filter": obj({"tag": {"type": "string"}})}),
        obj({"filter": obj({"tag": {"type": "string"}, "color": {"type": "string"}})}),
        [
            (
                "informational",
                "arg.optional.added",
                "parameters.properties.filter.properties.color",
            )
        ],
    ),
    (
        "nested-object-required-added",
        obj({"filter": obj({"tag": {"type": "string"}})}),
        obj({"filter": obj({"tag": {"type": "string"}}, ["tag"])}),
        [
            (
                "breaking",
                "arg.required.added",
                "parameters.properties.filter.required.tag",
            )
        ],
    ),
    (
        "array-items-type-incompatible",
        obj({"tags": {"type": "array", "items": {"type": "string"}}}),
        obj({"tags": {"type": "array", "items": {"type": "integer"}}}),
        [("breaking", "type.incompatible", "parameters.properties.tags.items.type")],
    ),
    (
        "array-items-enum-widened",
        obj({"tags": {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}}}),
        obj(
            {"tags": {"type": "array", "items": {"type": "string", "enum": ["a", "b", "c"]}}}
        ),
        [("risky", "enum.widened", "parameters.properties.tags.items.enum")],
    ),
    (
        "array-min-items-added",
        obj({"tags": {"type": "array", "items": {"type": "string"}}}),
        obj({"tags": {"type": "array", "items": {"type": "string"}, "minItems": 1}}),
        [("risky", "constraint.tightened", "parameters.properties.tags.minItems")],
    ),
    (
        "union-anyof-branch-removed",
        obj({"value": {"anyOf": [{"type": "string"}, {"type": "number"}]}}),
        obj({"value": {"anyOf": [{"type": "string"}]}}),
        [("risky", "union.changed", "parameters.properties.value.anyOf")],
    ),
    (
        "union-anyof-reordered",
        obj({"value": {"anyOf": [{"type": "string"}, {"type": "number"}]}}),
        obj({"value": {"anyOf": [{"type": "number"}, {"type": "string"}]}}),
        [],
    ),
    (
        "union-anyof-branch-incompatible",
        obj({"value": {"anyOf": [{"type": "string"}, {"type": "number"}]}}),
        obj({"value": {"anyOf": [{"type": "string"}, {"type": "boolean"}]}}),
        [
            (
                "breaking",
                "type.incompatible",
                "parameters.properties.value.anyOf[1].type",
            )
        ],
    ),
    (
        "union-anyof-added",
        obj({"value": {"type": "string"}}),
        obj({"value": {"type": "string", "anyOf": [{"const": "a"}, {"const": "b"}]}}),
        [("risky", "union.changed", "parameters.properties.value.anyOf")],
    ),
    (
        "enum-narrowed",
        obj({"order": {"type": "string", "enum": ["relevance", "recency", "title"]}}),
        obj({"order": {"type": "string", "enum": ["relevance", "recency"]}}),
        [("breaking", "enum.narrowed", "parameters.properties.order.enum")],
    ),
    (
        "enum-widened",
        obj({"order": {"type": "string", "enum": ["relevance", "recency"]}}),
        obj({"order": {"type": "string", "enum": ["relevance", "recency", "title"]}}),
        [("risky", "enum.widened", "parameters.properties.order.enum")],
    ),
    (
        "enum-reordered-is-a-set",
        obj({"order": {"type": "string", "enum": ["relevance", "recency", "title"]}}),
        obj({"order": {"type": "string", "enum": ["title", "relevance", "recency"]}}),
        [("informational", "enum.reordered", "parameters.properties.order.enum")],
    ),
    (
        "enum-added",
        obj({"order": {"type": "string"}}),
        obj({"order": {"type": "string", "enum": ["relevance"]}}),
        [("breaking", "enum.narrowed", "parameters.properties.order.enum")],
    ),
    (
        "enum-removed",
        obj({"order": {"type": "string", "enum": ["relevance"]}}),
        obj({"order": {"type": "string"}}),
        [("risky", "enum.widened", "parameters.properties.order.enum")],
    ),
    (
        "required-added-for-existing-property",
        obj({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
        obj({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query", "limit"]),
        [("breaking", "arg.required.added", "parameters.required.limit")],
    ),
    (
        "required-removed",
        obj({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query", "limit"]),
        obj({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
        [("risky", "arg.required.removed", "parameters.required.limit")],
    ),
    (
        "required-property-added",
        obj({"query": {"type": "string"}}, ["query"]),
        obj({"query": {"type": "string"}, "offset": {"type": "integer"}}, ["query", "offset"]),
        [("breaking", "arg.required.added", "parameters.properties.offset")],
    ),
    (
        "optional-property-added",
        obj({"query": {"type": "string"}}, ["query"]),
        obj({"query": {"type": "string"}, "offset": {"type": "integer"}}, ["query"]),
        [("informational", "arg.optional.added", "parameters.properties.offset")],
    ),
    (
        "property-removed",
        obj({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
        obj({"query": {"type": "string"}}, ["query"]),
        [("breaking", "arg.removed", "parameters.properties.limit")],
    ),
    (
        "description-only",
        obj({"query": {"type": "string", "description": "A query."}}),
        obj({"query": {"type": "string", "description": "A full-text query."}}),
        [("informational", "description.changed", "parameters.properties.query.description")],
    ),
    (
        "examples-only",
        obj({"query": {"type": "string"}}),
        obj({"query": {"type": "string", "examples": ["quarterly report"]}}),
        [("informational", "examples.changed", "parameters.properties.query.examples")],
    ),
    (
        "type-incompatible",
        obj({"limit": {"type": "string"}}),
        obj({"limit": {"type": "integer"}}),
        [("breaking", "type.incompatible", "parameters.properties.limit.type")],
    ),
    (
        "integer-to-number-is-widening",
        obj({"limit": {"type": "integer"}}),
        obj({"limit": {"type": "number"}}),
        [("risky", "type.widened", "parameters.properties.limit.type")],
    ),
]


@pytest.mark.parametrize(
    ("baseline", "candidate", "expected"),
    [case[1:] for case in SCHEMA_CASES],
    ids=[case[0] for case in SCHEMA_CASES],
)
def test_input_schema_rules(baseline, candidate, expected):
    changes = classify([tool(parameters=baseline)], [tool(parameters=candidate)])
    assert triples(changes) == expected


# --- output schema rules ---------------------------------------------------

OUTPUT_BASE = obj({"total": {"type": "integer"}, "matches": {"type": "array"}})

OUTPUT_CASES = [
    (
        "output-field-removed-is-breaking",
        OUTPUT_BASE,
        obj({"matches": {"type": "array"}}),
        [("breaking", "output.field.removed", "output.properties.total")],
    ),
    (
        "output-optional-field-added-is-informational",
        OUTPUT_BASE,
        obj(
            {
                "total": {"type": "integer"},
                "matches": {"type": "array"},
                "elapsed_ms": {"type": "integer"},
            }
        ),
        [("informational", "output.field.optional.added", "output.properties.elapsed_ms")],
    ),
    (
        "output-schema-removed-entirely",
        OUTPUT_BASE,
        None,
        [("breaking", "output.field.removed", "output")],
    ),
    (
        "output-schema-added-entirely",
        None,
        OUTPUT_BASE,
        [("informational", "output.field.optional.added", "output")],
    ),
    (
        "output-type-incompatible",
        OUTPUT_BASE,
        obj({"total": {"type": "string"}, "matches": {"type": "array"}}),
        [("breaking", "output.type.incompatible", "output.properties.total.type")],
    ),
    (
        "output-required-added-is-not-breaking-for-callers",
        OUTPUT_BASE,
        obj({"total": {"type": "integer"}, "matches": {"type": "array"}}, ["total"]),
        [("risky", "schema.changed", "output.required.total")],
    ),
    (
        "output-nested-field-removed",
        obj({"matches": {"type": "array", "items": obj({"id": {"type": "string"}})}}),
        obj({"matches": {"type": "array", "items": obj({})}}),
        [
            (
                "breaking",
                "output.field.removed",
                "output.properties.matches.items.properties.id",
            )
        ],
    ),
]


@pytest.mark.parametrize(
    ("baseline", "candidate", "expected"),
    [case[1:] for case in OUTPUT_CASES],
    ids=[case[0] for case in OUTPUT_CASES],
)
def test_output_schema_rules(baseline, candidate, expected):
    changes = classify([tool(output=baseline)], [tool(output=candidate)])
    assert triples(changes) == expected


# --- roster rules ----------------------------------------------------------

ROSTER_CASES = [
    (
        "tool-removed",
        ["search_documents", "create_ticket"],
        ["search_documents"],
        [("breaking", "tool.removed", "")],
    ),
    (
        "tool-added",
        ["search_documents"],
        ["search_documents", "create_ticket"],
        [("informational", "tool.added", "")],
    ),
    (
        "roster-reordered",
        ["search_documents", "create_ticket"],
        ["create_ticket", "search_documents"],
        [("informational", "roster.reordered", "")],
    ),
    (
        "roster-unchanged",
        ["search_documents", "create_ticket"],
        ["search_documents", "create_ticket"],
        [],
    ),
]


@pytest.mark.parametrize(
    ("baseline", "candidate", "expected"),
    [case[1:] for case in ROSTER_CASES],
    ids=[case[0] for case in ROSTER_CASES],
)
def test_roster_rules(baseline, candidate, expected):
    changes = classify(
        [tool(name) for name in baseline], [tool(name) for name in candidate]
    )
    assert triples(changes) == expected


def test_tool_description_change_is_informational():
    changes = classify([tool()], [tool(description="Search the corpus and rank matches.")])
    assert triples(changes) == [("informational", "description.changed", "description")]


def test_removed_tool_is_reported_against_its_own_name():
    changes = classify([tool("a"), tool("b")], [tool("a")])
    assert [(change.tool, change.rule) for change in changes] == [("b", "tool.removed")]


# --- canonicalization, ordering, and helpers -------------------------------


def test_key_order_and_description_whitespace_are_not_changes():
    baseline = tool(
        description="Search the corpus.",
        parameters={
            "properties": {"query": {"description": "A query.", "type": "string"}},
            "type": "object",
        },
    )
    candidate = tool(
        description="Search\n  the   corpus.\n",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "  A  query. "}},
        },
    )
    assert classify([baseline], [candidate]) == []


def test_changes_are_sorted_most_severe_first():
    baseline = [
        tool(
            parameters=obj({"query": {"type": "string"}, "limit": {"type": "integer"}}),
            output=OUTPUT_BASE,
        ),
        tool("create_ticket"),
    ]
    candidate = [
        tool(
            description="Search and rank.",
            parameters=obj({"query": {"type": "integer"}, "offset": {"type": "integer"}}),
            output=OUTPUT_BASE,
        )
    ]
    changes = classify(baseline, candidate)
    severities = [change.severity for change in changes]
    assert severities == sorted(
        severities, key=lambda name: ["breaking", "risky", "informational"].index(name)
    )
    assert severities[0] == "breaking"
    # Ordering is stable across runs.
    assert triples(classify(baseline, candidate)) == triples(changes)


def test_count_by_severity_counts_every_bucket():
    baseline = [
        tool(parameters=obj({"query": {"type": "string"}, "limit": {"type": "integer"}}))
    ]
    candidate = [
        tool(
            description="Search and rank.",
            parameters=obj(
                {
                    "query": {"type": "string"},
                    "limit": {"type": "number"},
                    "offset": {"type": "integer"},
                }
            ),
        )
    ]
    counts = count_by_severity(classify(baseline, candidate))
    assert counts == {"breaking": 0, "risky": 1, "informational": 2}
    assert list(counts) == ["breaking", "risky", "informational"]


def test_count_by_severity_of_no_changes_is_all_zero():
    assert count_by_severity([]) == {"breaking": 0, "risky": 0, "informational": 0}


@pytest.mark.parametrize(
    ("candidate_type", "threshold", "expected"),
    [
        ("integer", "breaking", False),
        ("integer", "risky", False),
        ("integer", "informational", False),
        ("number", "breaking", False),
        ("number", "risky", True),
        ("number", "informational", True),
        ("boolean", "breaking", True),
        ("boolean", "risky", True),
        ("boolean", "informational", True),
    ],
)
def test_reaches_compares_against_the_threshold(candidate_type, threshold, expected):
    changes = classify(
        [tool(parameters=obj({"limit": {"type": "integer"}}))],
        [tool(parameters=obj({"limit": {"type": candidate_type}}))],
    )
    assert reaches(changes, threshold) is expected


@pytest.mark.parametrize(
    ("threshold", "expected"),
    [("breaking", False), ("risky", False), ("informational", True)],
)
def test_reaches_on_informational_only_changes(threshold, expected):
    changes = classify([tool()], [tool(description="Search and rank the matches.")])
    assert reaches(changes, threshold) is expected


def test_reaches_is_false_for_an_unknown_severity():
    changes = classify([tool()], [tool(name="renamed")])
    assert changes
    assert reaches(changes, "never") is False


def test_change_to_dict_carries_before_and_after():
    changes = classify(
        [tool(parameters=obj({"order": {"type": "string", "enum": ["a", "b"]}}))],
        [tool(parameters=obj({"order": {"type": "string", "enum": ["a"]}}))],
    )
    assert len(changes) == 1
    payload = changes[0].to_dict()
    assert payload["severity"] == "breaking"
    assert payload["rule"] == "enum.narrowed"
    assert payload["tool"] == "search_documents"
    assert payload["before"] == ["a", "b"]
    assert payload["after"] == ["a"]
    assert list(payload) == [
        "severity",
        "rule",
        "tool",
        "path",
        "message",
        "before",
        "after",
    ]
