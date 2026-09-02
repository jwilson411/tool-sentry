"""Golden tests for the SARIF renderer and its GitHub annotations.

The example pair under ``examples/sarif/`` is the fixture the bundled
composite action is documented against, so its rendered results are frozen
here: a change to the classifier that moves a rule id or a level shows up as
a failing golden rather than as a silently different annotation.
"""

from __future__ import annotations

import json

import pytest
from conftest import ROOT

from tool_sentry import __version__, classify
from tool_sentry.adapters import load_tools
from tool_sentry.classify import Change
from tool_sentry.cli import main
from tool_sentry.sarif import (
    SARIF_SCHEMA,
    SARIF_VERSION,
    render_annotations,
    render_sarif,
)
from tool_sentry.sarif import main as sarif_main

EXAMPLES = ROOT / "examples" / "sarif"
EXAMPLE_BASELINE = EXAMPLES / "baseline.json"
EXAMPLE_CANDIDATE = EXAMPLES / "candidate.json"

#: Every result the example pair produces: rule id, level, location.
GOLDEN_RESULTS = [
    ("arg.removed", "error", "create_ticket.parameters.properties.title"),
    ("arg.required.added", "error", "create_ticket.parameters.properties.summary"),
    ("tool.removed", "error", "export_report"),
    ("arg.required.added", "error", "search_documents.parameters.required.limit"),
    ("enum.narrowed", "error", "search_documents.parameters.properties.order.enum"),
    ("output.field.removed", "error", "search_documents.output.properties.total"),
    ("enum.widened", "warning", "create_ticket.parameters.properties.severity.enum"),
    ("arg.optional.added", "note", "search_documents.parameters.properties.offset"),
]

#: Breaking classes the example candidate is required to demonstrate.
GOLDEN_BREAKING_RULES = {
    "arg.removed",
    "arg.required.added",
    "enum.narrowed",
    "output.field.removed",
    "tool.removed",
}


def run(args, capsys):
    code = main(args)
    captured = capsys.readouterr()
    return code, captured.out.strip(), captured.err.strip()


def tools_of(path):
    return load_tools(json.loads(path.read_text(encoding="utf-8")))


@pytest.fixture
def example_changes() -> list[Change]:
    return classify(tools_of(EXAMPLE_BASELINE), tools_of(EXAMPLE_CANDIDATE))


@pytest.fixture
def example_log(example_changes) -> dict:
    return json.loads(render_sarif(example_changes, tool_version=__version__))


# --- log envelope ----------------------------------------------------------


def test_sarif_envelope(example_log):
    assert example_log["version"] == SARIF_VERSION == "2.1.0"
    assert example_log["$schema"] == SARIF_SCHEMA
    assert list(example_log) == ["$schema", "version", "runs"]
    assert len(example_log["runs"]) == 1


def test_driver_identifies_tool_sentry_and_its_version(example_log):
    driver = example_log["runs"][0]["tool"]["driver"]
    assert driver["name"] == "tool-sentry"
    assert driver["version"] == __version__
    assert driver["informationUri"].startswith("https://")


def test_driver_rules_are_the_unique_rule_ids_sorted(example_log):
    run_ = example_log["runs"][0]
    rules = run_["tool"]["driver"]["rules"]
    ids = [rule["id"] for rule in rules]
    assert ids == sorted({result["ruleId"] for result in run_["results"]})
    for rule in rules:
        assert rule["defaultConfiguration"]["level"] in {"error", "warning", "note"}
        assert rule["name"] and " " not in rule["name"]
        assert rule["shortDescription"]["text"]
        assert rule["properties"]["severity"] in {
            "breaking",
            "risky",
            "informational",
        }


def test_rule_default_level_matches_the_severity_it_carries(example_log):
    run_ = example_log["runs"][0]
    levels = {rule["id"]: rule["defaultConfiguration"]["level"] for rule in run_["tool"]["driver"]["rules"]}
    for result in run_["results"]:
        assert levels[result["ruleId"]] == result["level"]


def test_hashes_are_recorded_on_the_run_when_given(example_changes):
    log = json.loads(
        render_sarif(
            example_changes,
            tool_version=__version__,
            baseline_hash="sha256:aaa",
            candidate_hash="sha256:bbb",
        )
    )
    assert log["runs"][0]["properties"] == {
        "baselineHash": "sha256:aaa",
        "candidateHash": "sha256:bbb",
    }


def test_hashes_are_omitted_when_not_given(example_log):
    assert "properties" not in example_log["runs"][0]


def test_clean_diff_renders_an_empty_run():
    log = json.loads(render_sarif([], tool_version=__version__))
    assert log["runs"][0]["results"] == []
    assert log["runs"][0]["tool"]["driver"]["rules"] == []


# --- results ---------------------------------------------------------------


def test_every_result_carries_a_rule_level_location_and_properties(example_log):
    results = example_log["runs"][0]["results"]
    assert results
    for result in results:
        assert list(result) == [
            "ruleId",
            "level",
            "message",
            "logicalLocations",
            "properties",
        ]
        assert result["ruleId"]
        assert result["level"] in {"error", "warning", "note"}
        assert result["message"]["text"]
        location = result["logicalLocations"][0]
        assert location["fullyQualifiedName"]
        assert location["name"] == location["fullyQualifiedName"].rsplit(".", 1)[-1]
        assert set(result["properties"]) == {"severity", "tool", "path"}


def test_example_results_are_golden(example_log):
    rendered = [
        (
            result["ruleId"],
            result["level"],
            result["logicalLocations"][0]["fullyQualifiedName"],
        )
        for result in example_log["runs"][0]["results"]
    ]
    assert rendered == GOLDEN_RESULTS


def test_result_properties_repeat_the_tool_and_path(example_log, example_changes):
    for result, change in zip(example_log["runs"][0]["results"], example_changes):
        assert result["properties"] == {
            "severity": change.severity,
            "tool": change.tool,
            "path": change.path,
        }
        assert result["message"]["text"] == change.message


@pytest.mark.parametrize(
    "severity,level",
    [("breaking", "error"), ("risky", "warning"), ("informational", "note")],
)
def test_severity_maps_to_level(severity, level):
    change = Change(severity, "schema.changed", "t", "parameters.x", "changed")
    log = json.loads(render_sarif([change], tool_version=__version__))
    assert log["runs"][0]["results"][0]["level"] == level


def test_roster_level_change_gets_a_location(example_log):
    change = Change("informational", "roster.reordered", "", "", "order changed")
    log = json.loads(render_sarif([change], tool_version=__version__))
    location = log["runs"][0]["results"][0]["logicalLocations"][0]
    assert location["fullyQualifiedName"] == "contract"


# --- CLI -------------------------------------------------------------------


def test_cli_diff_format_sarif_parses_and_keeps_exit_code(capsys):
    code, stdout, stderr = run(
        ["diff", str(EXAMPLE_BASELINE), str(EXAMPLE_CANDIDATE), "--format", "sarif"],
        capsys,
    )
    assert code == 1
    assert stderr == ""
    log = json.loads(stdout)
    assert log["version"] == "2.1.0"
    assert log["runs"][0]["properties"]["baselineHash"].startswith("sha256:")
    assert log["runs"][0]["properties"]["candidateHash"].startswith("sha256:")
    assert [result["ruleId"] for result in log["runs"][0]["results"]] == [
        rule for rule, _, _ in GOLDEN_RESULTS
    ]


def test_cli_diff_sarif_of_a_clean_pair_exits_0(capsys):
    code, stdout, _ = run(
        ["diff", str(EXAMPLE_BASELINE), str(EXAMPLE_BASELINE), "--format", "sarif"],
        capsys,
    )
    assert code == 0
    assert json.loads(stdout)["runs"][0]["results"] == []


def test_cli_diff_sarif_respects_fail_on_never(capsys):
    code, _, _ = run(
        [
            "diff",
            str(EXAMPLE_BASELINE),
            str(EXAMPLE_CANDIDATE),
            "--format",
            "sarif",
            "--fail-on",
            "never",
        ],
        capsys,
    )
    assert code == 0


def test_cli_check_format_sarif(tmp_path, capsys):
    policy = tmp_path / "policy.yml"
    policy.write_text("version: 1\nfail_on: breaking\n", encoding="utf-8")
    code, stdout, _ = run(
        [
            "check",
            "--baseline",
            str(EXAMPLE_BASELINE),
            "--candidate",
            str(EXAMPLE_CANDIDATE),
            "--policy",
            str(policy),
            "--format",
            "sarif",
        ],
        capsys,
    )
    assert code == 1
    log = json.loads(stdout)
    assert log["version"] == "2.1.0"
    assert [result["ruleId"] for result in log["runs"][0]["results"]] == [
        rule for rule, _, _ in GOLDEN_RESULTS
    ]


def test_cli_check_sarif_drops_ignored_paths(tmp_path, capsys):
    policy = tmp_path / "policy.yml"
    policy.write_text(
        "version: 1\nfail_on: breaking\nignore_paths:\n"
        "  - parameters.properties.offset\n",
        encoding="utf-8",
    )
    code, stdout, _ = run(
        [
            "check",
            "--baseline",
            str(EXAMPLE_BASELINE),
            "--candidate",
            str(EXAMPLE_CANDIDATE),
            "--policy",
            str(policy),
            "--format",
            "sarif",
        ],
        capsys,
    )
    assert code == 1
    paths = {
        result["properties"]["path"] for result in json.loads(stdout)["runs"][0]["results"]
    }
    assert "parameters.properties.offset" not in paths


def test_json_report_keys_are_unchanged(capsys):
    code, stdout, _ = run(
        ["diff", str(EXAMPLE_BASELINE), str(EXAMPLE_CANDIDATE), "--format", "json"],
        capsys,
    )
    assert code == 1
    report = json.loads(stdout)
    assert list(report) == ["baseline_hash", "candidate_hash", "counts", "changes"]
    assert report["counts"] == {"breaking": 6, "risky": 1, "informational": 1}
    assert [change["rule"] for change in report["changes"]] == [
        rule for rule, _, _ in GOLDEN_RESULTS
    ]


# --- annotations -----------------------------------------------------------


def test_annotations_cover_every_result(example_log):
    lines = render_annotations(example_log, file="examples/sarif/candidate.json")
    assert len(lines) == len(example_log["runs"][0]["results"])
    for line in lines:
        assert line.startswith("::")
        assert "file=examples/sarif/candidate.json" in line


def test_annotations_report_at_least_three_breaking_rule_ids(example_log):
    lines = render_annotations(example_log, file="examples/sarif/candidate.json")
    errors = [line for line in lines if line.startswith("::error ")]
    rules = {
        line.split("title=", 1)[1].split(" (", 1)[0] for line in errors
    }
    assert rules == GOLDEN_BREAKING_RULES
    assert len(rules) >= 3


def test_annotation_line_is_a_workflow_command(example_log):
    lines = render_annotations(example_log, file="candidate.json")
    assert lines[2] == (
        "::error file=candidate.json,title=tool.removed (breaking)"
        "::export_report: tool 'export_report' was removed"
    )


@pytest.mark.parametrize(
    "level,command", [("error", "error"), ("warning", "warning"), ("note", "notice")]
)
def test_level_maps_to_workflow_command(level, command):
    document = {
        "runs": [
            {
                "results": [
                    {
                        "ruleId": "schema.changed",
                        "level": level,
                        "message": {"text": "changed"},
                        "logicalLocations": [{"fullyQualifiedName": "t.parameters"}],
                        "properties": {"severity": "risky"},
                    }
                ]
            }
        ]
    }
    assert render_annotations(document, file="c.json")[0].startswith(f"::{command} ")


def test_annotation_escapes_commas_colons_and_newlines():
    document = {
        "runs": [
            {
                "results": [
                    {
                        "ruleId": "schema.changed",
                        "level": "warning",
                        "message": {"text": "a\nb 100%"},
                        "logicalLocations": [{"fullyQualifiedName": "t.parameters"}],
                        "properties": {"severity": "risky"},
                    }
                ]
            }
        ]
    }
    line = render_annotations(document, file="dir,with:punct/c.json")[0]
    assert "file=dir%2Cwith%3Apunct/c.json" in line
    assert "%0Ab 100%25" in line
    assert "\n" not in line


def test_module_entry_point_prints_annotations(tmp_path, capsys):
    log = tmp_path / "tool-sentry.sarif"
    code, stdout, _ = run(
        ["diff", str(EXAMPLE_BASELINE), str(EXAMPLE_CANDIDATE), "--format", "sarif"],
        capsys,
    )
    assert code == 1
    log.write_text(stdout, encoding="utf-8")

    assert sarif_main([str(log), "--file", "candidate.json"]) == 0
    printed = capsys.readouterr().out.strip().splitlines()
    assert len(printed) == len(GOLDEN_RESULTS)
    assert sum(line.startswith("::error ") for line in printed) == 6


def test_module_entry_point_exits_2_on_a_missing_log(tmp_path, capsys):
    assert sarif_main([str(tmp_path / "nope.sarif"), "--file", "c.json"]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_module_entry_point_exits_2_on_a_non_sarif_document(tmp_path, capsys):
    bad = tmp_path / "bad.sarif"
    bad.write_text("[]", encoding="utf-8")
    assert sarif_main([str(bad), "--file", "c.json"]) == 2
    assert "not a SARIF log" in capsys.readouterr().err
