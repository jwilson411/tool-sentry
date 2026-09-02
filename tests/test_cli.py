from __future__ import annotations

import hashlib
import json
import shutil

import pytest
from conftest import FIXTURES

from tool_sentry.canonicalize import canonical_json
from tool_sentry.cli import main


def run(args, capsys):
    code = main(args)
    captured = capsys.readouterr()
    return code, captured.out.strip(), captured.err.strip()


def test_snapshot_writes_lock_file(tmp_path, capsys):
    out = tmp_path / "tools.lock.json"
    code, stdout, _ = run(
        ["snapshot", str(FIXTURES / "openai_tools.json"), "--out", str(out)], capsys
    )
    assert code == 0
    lock = json.loads(out.read_text(encoding="utf-8"))
    assert lock["version"] == 1
    assert stdout == lock["hash"]

    payload = canonical_json(lock["tools"]).encode("utf-8")
    assert lock["hash"] == "sha256:" + hashlib.sha256(payload).hexdigest()


def test_lock_file_is_pretty_printed(tmp_path, capsys):
    out = tmp_path / "tools.lock.json"
    run(["snapshot", str(FIXTURES / "mcp_tools_list.json"), "--out", str(out)], capsys)
    text = out.read_text(encoding="utf-8")
    assert text.startswith('{\n  "version": 1,')
    assert text.endswith("}\n")


def test_out_defaults_to_cwd(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code, stdout, _ = run(["snapshot", str(FIXTURES / "anthropic_tools.json")], capsys)
    assert code == 0
    lock = json.loads((tmp_path / "tools.lock.json").read_text(encoding="utf-8"))
    assert lock["hash"] == stdout


def test_all_dialects_produce_the_same_cli_hash(tmp_path, capsys):
    hashes = set()
    for name in ("openai_tools.json", "anthropic_tools.json", "mcp_tools_list.json"):
        out = tmp_path / f"{name}.lock"
        code, stdout, _ = run(["snapshot", str(FIXTURES / name), "--out", str(out)], capsys)
        assert code == 0
        hashes.add(stdout)
    assert len(hashes) == 1


def test_directory_input_concatenates_in_filename_order(tmp_path, capsys):
    for name in ("b_openai.json", "a_mcp.json"):
        source = "openai_tools.json" if "openai" in name else "mcp_tools_list.json"
        shutil.copy(FIXTURES / source, tmp_path / name)
    out = tmp_path / "dir.lock"
    code, stdout, _ = run(["snapshot", str(tmp_path), "--out", str(out)], capsys)
    assert code == 0
    lock = json.loads(out.read_text(encoding="utf-8"))
    # a_mcp.json first, then b_openai.json: four tools total.
    assert [tool["name"] for tool in lock["tools"]] == [
        "search_documents",
        "create_ticket",
        "search_documents",
        "create_ticket",
    ]
    assert lock["hash"] == stdout


def test_unrecognized_input_exits_2(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"messages": []}), encoding="utf-8")
    code, _, stderr = run(["snapshot", str(bad)], capsys)
    assert code == 2
    assert "unrecognized tool document" in stderr


def test_invalid_json_exits_2(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    code, _, stderr = run(["snapshot", str(bad)], capsys)
    assert code == 2
    assert "not valid JSON" in stderr


def test_missing_file_exits_2(tmp_path, capsys):
    code, _, stderr = run(["snapshot", str(tmp_path / "nope.json")], capsys)
    assert code == 2
    assert "no such file or directory" in stderr


def test_empty_directory_exits_2(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    code, _, stderr = run(["snapshot", str(empty)], capsys)
    assert code == 2
    assert "no .json files" in stderr


# --- diff ------------------------------------------------------------------

CLASSIFY = FIXTURES / "classify"
BASELINE = CLASSIFY / "baseline.json"
BREAKING = CLASSIFY / "candidate_breaking.json"
INFORMATIONAL = CLASSIFY / "candidate_informational.json"


def lock_of(source, tmp_path, capsys, name=None):
    """Snapshot a dialect fixture and return the lock file path."""
    out = tmp_path / (name or f"{source.stem}.lock.json")
    code, _, stderr = run(["snapshot", str(source), "--out", str(out)], capsys)
    assert code == 0, stderr
    return out


def diff_report(args, capsys):
    """Run ``diff --format json`` and return (exit code, parsed report)."""
    code, stdout, stderr = run(["diff", *args, "--format", "json"], capsys)
    assert stderr == ""
    return code, json.loads(stdout)


def test_diff_lock_against_lock(tmp_path, capsys):
    baseline = lock_of(BASELINE, tmp_path, capsys)
    candidate = lock_of(BREAKING, tmp_path, capsys)
    code, report = diff_report([str(baseline), str(candidate)], capsys)
    assert code == 1
    rules = {change["rule"] for change in report["changes"]}
    assert "tool.removed" in rules
    assert report["counts"]["breaking"] > 0


def test_diff_dialect_json_against_lock_matches_lock_against_lock(tmp_path, capsys):
    baseline_lock = lock_of(BASELINE, tmp_path, capsys)
    candidate_lock = lock_of(BREAKING, tmp_path, capsys)

    lock_code, lock_report = diff_report(
        [str(baseline_lock), str(candidate_lock)], capsys
    )
    mixed_code, mixed_report = diff_report([str(BASELINE), str(candidate_lock)], capsys)
    dialect_code, dialect_report = diff_report([str(BASELINE), str(BREAKING)], capsys)

    assert lock_code == mixed_code == dialect_code == 1
    # The dialect a contract is written in is not part of the contract.
    assert lock_report == mixed_report == dialect_report


def test_diff_of_identical_snapshots_is_clean(tmp_path, capsys):
    baseline = lock_of(BASELINE, tmp_path, capsys, name="a.lock.json")
    candidate = lock_of(BASELINE, tmp_path, capsys, name="b.lock.json")
    code, stdout, stderr = run(["diff", str(baseline), str(candidate)], capsys)
    assert code == 0
    assert stderr == ""
    assert stdout == "No contract changes."


def test_diff_of_a_snapshot_against_its_source_is_clean(tmp_path, capsys):
    baseline = lock_of(BASELINE, tmp_path, capsys)
    code, stdout, _ = run(["diff", str(BASELINE), str(baseline)], capsys)
    assert code == 0
    assert stdout == "No contract changes."


def test_breaking_change_fails_by_default(capsys):
    code, stdout, _ = run(["diff", str(BASELINE), str(BREAKING)], capsys)
    assert code == 1
    assert "breaking" in stdout
    assert "tool.removed" in stdout


def test_informational_change_passes_by_default(capsys):
    code, stdout, _ = run(["diff", str(BASELINE), str(INFORMATIONAL)], capsys)
    assert code == 0
    assert "informational" in stdout
    assert "breaking" not in stdout
    assert "risky" not in stdout


def test_fail_on_informational_fails_on_any_change(capsys):
    code, _, _ = run(
        ["diff", str(BASELINE), str(INFORMATIONAL), "--fail-on", "informational"], capsys
    )
    assert code == 1


def test_fail_on_informational_still_passes_when_nothing_changed(capsys):
    code, stdout, _ = run(
        ["diff", str(BASELINE), str(BASELINE), "--fail-on", "informational"], capsys
    )
    assert code == 0
    assert stdout == "No contract changes."


@pytest.mark.parametrize("candidate", [BREAKING, INFORMATIONAL])
def test_fail_on_never_always_exits_0(candidate, capsys):
    code, _, _ = run(
        ["diff", str(BASELINE), str(candidate), "--fail-on", "never"], capsys
    )
    assert code == 0


def test_fail_on_risky_ignores_informational_only_changes(capsys):
    code, _, _ = run(
        ["diff", str(BASELINE), str(INFORMATIONAL), "--fail-on", "risky"], capsys
    )
    assert code == 0


def test_json_report_shape(tmp_path, capsys):
    code, report = diff_report([str(BASELINE), str(BREAKING)], capsys)
    assert code == 1
    assert list(report) == ["baseline_hash", "candidate_hash", "counts", "changes"]
    assert report["baseline_hash"].startswith("sha256:")
    assert report["candidate_hash"].startswith("sha256:")
    assert report["baseline_hash"] != report["candidate_hash"]
    assert list(report["counts"]) == ["breaking", "risky", "informational"]
    assert sum(report["counts"].values()) == len(report["changes"])
    for change in report["changes"]:
        assert set(change) == {
            "severity",
            "rule",
            "tool",
            "path",
            "message",
            "before",
            "after",
        }

    # The reported hashes are the same ones `snapshot` prints.
    baseline_lock = json.loads(
        lock_of(BASELINE, tmp_path, capsys).read_text(encoding="utf-8")
    )
    assert report["baseline_hash"] == baseline_lock["hash"]


def test_json_flag_is_an_alias_for_format_json(capsys):
    _, aliased, _ = run(["diff", str(BASELINE), str(BREAKING), "--json"], capsys)
    _, explicit, _ = run(
        ["diff", str(BASELINE), str(BREAKING), "--format", "json"], capsys
    )
    assert aliased == explicit


def test_diff_of_missing_file_exits_2(tmp_path, capsys):
    code, _, stderr = run(["diff", str(BASELINE), str(tmp_path / "nope.json")], capsys)
    assert code == 2
    assert "no such file or directory" in stderr
