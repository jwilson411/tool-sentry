from __future__ import annotations

import json

import pytest

from tool_sentry.classify import Change
from tool_sentry.model import Tool
from tool_sentry.policy import (
    Policy,
    PolicyError,
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

SAMPLE = """\
# Review policy for the document tools.
version: 1
fail_on: breaking          # breaking | risky | informational | never
ignore_paths:
  - parameters.properties.experimental
forbidden_tool_names:
  - "eval"
  - "shell_*"
tools:
  search_documents:
    fail_on: informational
    ignore_paths:
      - parameters.properties.offset
"""


def write(tmp_path, text, name="policy.yml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def change(severity="breaking", tool="alpha", path="parameters.properties.q"):
    return Change(severity, "rule.id", tool, path, "message")


def tool(name):
    return Tool(name=name, description="", parameters={}, source="openai")


# --- parsing ---------------------------------------------------------------


def test_documented_sample_parses(tmp_path):
    policy = load_policy(write(tmp_path, SAMPLE))
    assert policy.fail_on == "breaking"
    assert policy.ignore_paths == ("parameters.properties.experimental",)
    assert policy.forbidden_tool_names == ("eval", "shell_*")
    assert policy.tools == {
        "search_documents": ToolPolicy(
            fail_on="informational", ignore_paths=("parameters.properties.offset",)
        )
    }


def test_json_policy_with_the_same_keys_is_equivalent(tmp_path):
    from_yaml = load_policy(write(tmp_path, SAMPLE))
    equivalent = {
        "version": 1,
        "fail_on": "breaking",
        "ignore_paths": ["parameters.properties.experimental"],
        "forbidden_tool_names": ["eval", "shell_*"],
        "tools": {
            "search_documents": {
                "fail_on": "informational",
                "ignore_paths": ["parameters.properties.offset"],
            }
        },
    }
    from_json = load_policy(
        write(tmp_path, json.dumps(equivalent, indent=2), name="policy.json")
    )
    assert from_json.tools == from_yaml.tools
    assert from_json.ignore_paths == from_yaml.ignore_paths
    assert from_json.forbidden_tool_names == from_yaml.forbidden_tool_names


def test_optional_keys_default(tmp_path):
    policy = load_policy(write(tmp_path, "version: 1\n"))
    assert policy == Policy(path=str(tmp_path / "policy.yml"))


def test_empty_flow_collections_are_accepted(tmp_path):
    policy = load_policy(write(tmp_path, "version: 1\nignore_paths: []\ntools: {}\n"))
    assert policy.ignore_paths == ()
    assert policy.tools == {}


@pytest.mark.parametrize(
    "text",
    [
        "version: 1\nignore_paths: &paths\n  - a\n",
        "version: 1\nignore_paths: *paths\n",
        "version: 1\nfail_on: !!str breaking\n",
        "version: 1\ntools:\n  a:\n    <<: *defaults\n",
        "---\nversion: 1\n",
        "version: 1\nfail_on: |\n  breaking\n",
    ],
)
def test_unsupported_yaml_features_are_rejected(tmp_path, text):
    with pytest.raises(PolicyError) as exc:
        load_policy(write(tmp_path, text))
    assert "not supported" in str(exc.value)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   \n\n",
        "version 1\n",
        "version: 1\nfail_on: sometimes\n",
        "version: 1\nignore_paths: parameters\n",
        "version: 1\nignore_paths:\n  - ''\n",
        "version: 1\nforbidden_tool_names: 3\n",
        "version: 1\ntools:\n  alpha: breaking\n",
        "version: 1\ntools:\n  alpha:\n    fail_on: yes-please\n",
        "version: 1\n  fail_on: breaking\n",
        "version: 1\nversion: 1\n",
        'version: 1\nfail_on: "breaking\n',
        "- version\n- 1\n",
    ],
)
def test_malformed_policy_raises_one_error(tmp_path, text):
    with pytest.raises(PolicyError) as exc:
        load_policy(write(tmp_path, text))
    assert str(exc.value).count("\n") == 0


def test_missing_policy_file_raises(tmp_path):
    with pytest.raises(PolicyError) as exc:
        load_policy(tmp_path / "nope.yml")
    assert "cannot read policy" in str(exc.value)


def test_unknown_keys_are_listed_in_one_error(tmp_path):
    text = "version: 1\nfail_onn: breaking\ntools:\n  alpha:\n    ignore: a\n"
    with pytest.raises(PolicyError) as exc:
        load_policy(write(tmp_path, text))
    message = str(exc.value)
    assert "unknown policy keys" in message
    assert "fail_onn" in message
    assert "tools.alpha.ignore" in message


def test_missing_version_is_an_error(tmp_path):
    with pytest.raises(PolicyError) as exc:
        load_policy(write(tmp_path, "fail_on: breaking\n"))
    assert "version" in str(exc.value)


@pytest.mark.parametrize("version", ["2", "0", '"1"', "true"])
def test_stale_policy_version(tmp_path, version):
    with pytest.raises(PolicyError) as exc:
        load_policy(write(tmp_path, f"version: {version}\n"))
    message = str(exc.value)
    assert "stale policy format" in message and "version" in message


def test_build_policy_rejects_a_non_mapping():
    with pytest.raises(PolicyError):
        build_policy(["version"])


# --- ignore paths ----------------------------------------------------------


@pytest.mark.parametrize(
    "candidate,expected",
    [
        ("parameters.properties.offset", True),
        ("parameters.properties.offset.enum", True),
        ("parameters.properties.offsetting", False),
        ("parameters.properties", False),
        ("", False),
    ],
)
def test_path_ignored_matches_the_path_or_its_children(candidate, expected):
    assert path_ignored(candidate, ["parameters.properties.offset"]) is expected


def test_global_ignores_apply_to_every_tool():
    policy = Policy(ignore_paths=("parameters.properties.q",))
    changes = [change(tool="alpha"), change(tool="beta")]
    assert apply_ignores(policy, changes) == []


def test_per_tool_ignores_apply_only_to_that_tool():
    policy = Policy(
        tools={"alpha": ToolPolicy(ignore_paths=("parameters.properties.q",))}
    )
    kept = apply_ignores(policy, [change(tool="alpha"), change(tool="beta")])
    assert [item.tool for item in kept] == ["beta"]


# --- thresholds and forbidden names ----------------------------------------


def test_per_tool_threshold_replaces_the_global_one_for_that_tool():
    policy = Policy(fail_on="breaking", tools={"alpha": ToolPolicy(fail_on="informational")})
    assert reaches_threshold(policy, [change("informational", tool="alpha")]) is True
    assert reaches_threshold(policy, [change("informational", tool="beta")]) is False


def test_never_never_fails_on_severity():
    policy = Policy(fail_on="never")
    assert reaches_threshold(policy, [change("breaking")]) is False


def test_roster_changes_use_the_global_threshold():
    policy = Policy(fail_on="informational", tools={"alpha": ToolPolicy(fail_on="never")})
    roster = Change("informational", "roster.reordered", "", "", "order changed")
    assert reaches_threshold(policy, [roster]) is True


def test_forbidden_names_report_the_pattern_and_the_name():
    policy = Policy(forbidden_tool_names=("shell_*", "eval"))
    matches = forbidden_names(policy, [tool("shell_exec"), tool("search")])
    assert [entry.to_dict() for entry in matches] == [
        {"pattern": "shell_*", "name": "shell_exec"}
    ]


def test_forbidden_matching_is_case_sensitive():
    policy = Policy(forbidden_tool_names=("eval",))
    assert forbidden_names(policy, [tool("EVAL")]) == []


def test_evaluate_counts_ignored_changes_and_fails_on_forbidden_names():
    policy = Policy(
        fail_on="never",
        ignore_paths=("parameters.properties.q",),
        forbidden_tool_names=("al*",),
    )
    result = evaluate(policy, [change()], [tool("alpha")])
    assert result.changes == []
    assert result.ignored_count == 1
    assert result.failed is True


def test_duplicate_tool_names():
    assert duplicate_tool_names([tool("a"), tool("b"), tool("a")]) == ["a"]
    assert duplicate_tool_names([tool("a"), tool("b")]) == []
