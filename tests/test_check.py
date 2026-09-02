"""End-to-end CLI tests for ``check`` and ``approve``.

Everything here is file-based: fixtures, policies written into ``tmp_path``,
and lock files. Nothing is executed and no network call is made.
"""

from __future__ import annotations

import getpass
import json
import socket
import textwrap
from pathlib import Path

import pytest
from conftest import FIXTURES

from tool_sentry import __version__
from tool_sentry.cli import main

CLASSIFY = FIXTURES / "classify"
BASELINE = CLASSIFY / "baseline.json"
BREAKING = CLASSIFY / "candidate_breaking.json"
INFORMATIONAL = CLASSIFY / "candidate_informational.json"

FROZEN_AT = "2026-09-02T06:00:00Z"


def run(args, capsys):
    code = main(args)
    captured = capsys.readouterr()
    return code, captured.out.strip(), captured.err.strip()


def snapshot_hash(source, tmp_path, capsys, name):
    """Snapshot ``source`` into ``tmp_path`` and return (path, hash)."""
    out = tmp_path / name
    code, stdout, stderr = run(["snapshot", str(source), "--out", str(out)], capsys)
    assert code == 0, stderr
    return out, stdout


def write_policy(tmp_path, body, name="policy.yml"):
    path = tmp_path / name
    path.write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")
    return path


def baseline_doc():
    """A fresh, mutable copy of the baseline fixture document."""
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def tool_of(doc, name):
    return next(tool for tool in doc["tools"] if tool["name"] == name)


def write_doc(path, doc):
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def check_report(args, capsys):
    """Run ``check --format json`` and return (exit code, parsed report)."""
    code, stdout, stderr = run(["check", *args, "--format", "json"], capsys)
    assert stderr == ""
    return code, json.loads(stdout)


# --- global fail_on --------------------------------------------------------


def test_global_fail_on_breaking_passes_an_informational_candidate(tmp_path, capsys):
    policy = write_policy(tmp_path, "version: 1\nfail_on: breaking")
    code, stdout, stderr = run(
        [
            "check",
            "--baseline",
            str(BASELINE),
            "--candidate",
            str(INFORMATIONAL),
            "--policy",
            str(policy),
        ],
        capsys,
    )
    assert code == 0
    assert stderr == ""
    assert "informational" in stdout
    assert "breaking" not in stdout


def test_global_fail_on_breaking_fails_a_breaking_candidate(tmp_path, capsys):
    policy = write_policy(tmp_path, "version: 1\nfail_on: breaking")
    code, stdout, _ = run(
        [
            "check",
            "--baseline",
            str(BASELINE),
            "--candidate",
            str(BREAKING),
            "--policy",
            str(policy),
        ],
        capsys,
    )
    assert code == 1
    assert "tool.removed" in stdout


# --- per-tool fail_on ------------------------------------------------------


def test_per_tool_fail_on_can_fail_one_tool_while_global_is_breaking(tmp_path, capsys):
    policy = write_policy(
        tmp_path,
        """
        version: 1
        fail_on: breaking
        tools:
          search_documents:
            fail_on: informational
        """,
    )
    code, report = check_report(
        ["--baseline", str(BASELINE), "--candidate", str(INFORMATIONAL), "--policy", str(policy)],
        capsys,
    )
    assert code == 1
    assert report["counts"] == {"breaking": 0, "risky": 0, "informational": 5}


def test_per_tool_fail_on_only_applies_to_that_tool(tmp_path, capsys):
    # create_ticket is unchanged in the informational candidate, so lowering
    # only its threshold leaves the run passing.
    policy = write_policy(
        tmp_path,
        """
        version: 1
        fail_on: breaking
        tools:
          create_ticket:
            fail_on: informational
        """,
    )
    code, _, _ = run(
        [
            "check",
            "--baseline",
            str(BASELINE),
            "--candidate",
            str(INFORMATIONAL),
            "--policy",
            str(policy),
        ],
        capsys,
    )
    assert code == 0


# --- ignore_paths ----------------------------------------------------------


def narrowed_enum_candidate(tmp_path):
    """Baseline with search_documents' `order` enum narrowed: one breaking change."""
    doc = baseline_doc()
    tool_of(doc, "search_documents")["inputSchema"]["properties"]["order"]["enum"] = [
        "relevance",
        "recency",
    ]
    return write_doc(tmp_path / "narrowed.json", doc)


def test_ignore_paths_turns_a_failure_into_a_pass(tmp_path, capsys):
    candidate = narrowed_enum_candidate(tmp_path)
    strict = write_policy(tmp_path, "version: 1\nfail_on: breaking", name="strict.yml")
    code, report = check_report(
        ["--baseline", str(BASELINE), "--candidate", str(candidate), "--policy", str(strict)],
        capsys,
    )
    assert code == 1
    assert report["counts"]["breaking"] == 1
    assert report["ignored_count"] == 0

    # The ignore entry is a path prefix: `order` covers `order.enum`.
    lenient = write_policy(
        tmp_path,
        """
        version: 1
        fail_on: breaking
        ignore_paths:
          - parameters.properties.order
        """,
        name="lenient.yml",
    )
    code, report = check_report(
        ["--baseline", str(BASELINE), "--candidate", str(candidate), "--policy", str(lenient)],
        capsys,
    )
    assert code == 0
    assert report["changes"] == []
    assert report["ignored_count"] == 1


def test_per_tool_ignore_paths_only_apply_to_that_tool(tmp_path, capsys):
    # The same change, at the same path, on both tools.
    doc = baseline_doc()
    for name in ("search_documents", "create_ticket"):
        schema = tool_of(doc, name)["inputSchema"]
        schema["properties"]["debug"] = {"type": "boolean"}
        schema["required"] = [*schema["required"], "debug"]
    candidate = write_doc(tmp_path / "debug.json", doc)

    policy = write_policy(
        tmp_path,
        """
        version: 1
        fail_on: breaking
        tools:
          search_documents:
            ignore_paths:
              - parameters.properties.debug
        """,
    )
    code, report = check_report(
        ["--baseline", str(BASELINE), "--candidate", str(candidate), "--policy", str(policy)],
        capsys,
    )
    assert code == 1
    assert report["ignored_count"] == 1
    assert [
        (change["tool"], change["rule"])
        for change in report["changes"]
        if change["rule"] == "arg.required.added"
    ] == [("create_ticket", "arg.required.added")]


# --- forbidden tool names --------------------------------------------------


def test_forbidden_name_fails_identical_contracts(tmp_path, capsys):
    policy = write_policy(
        tmp_path,
        """
        version: 1
        fail_on: breaking
        forbidden_tool_names:
          - "create_*"
        """,
    )
    code, stdout, _ = run(
        [
            "check",
            "--baseline",
            str(BASELINE),
            "--candidate",
            str(BASELINE),
            "--policy",
            str(policy),
        ],
        capsys,
    )
    assert code == 1
    assert "No contract changes." in stdout
    assert "FORBIDDEN" in stdout
    assert "create_ticket" in stdout


def test_forbidden_patterns_are_case_sensitive(tmp_path, capsys):
    policy = write_policy(
        tmp_path,
        """
        version: 1
        forbidden_tool_names:
          - "CREATE_*"
        """,
    )
    code, report = check_report(
        ["--baseline", str(BASELINE), "--candidate", str(BASELINE), "--policy", str(policy)],
        capsys,
    )
    assert code == 0
    assert report["forbidden"] == []


# --- approve ---------------------------------------------------------------


def test_approval_round_trip(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("tool_sentry.snapshot.approved_at", lambda: FROZEN_AT)
    policy = write_policy(tmp_path, "version: 1\nfail_on: breaking")
    lock, _ = snapshot_hash(BASELINE, tmp_path, capsys, "tools.lock.json")

    args = ["--baseline", str(lock), "--candidate", str(BREAKING), "--policy", str(policy)]
    assert run(["check", *args], capsys)[0] == 1

    code, approved_hash, stderr = run(
        ["approve", "--baseline", str(lock), "--candidate", str(BREAKING)], capsys
    )
    assert code == 0, stderr

    # The same candidate now passes the same policy against the new baseline.
    code, report = check_report(args, capsys)
    assert code == 0
    assert report["changes"] == []

    text = lock.read_text(encoding="utf-8")
    written = json.loads(text)
    assert list(written) == ["version", "hash", "approved", "tools"]
    assert written["approved"] == {"tool_sentry": __version__, "at": FROZEN_AT}
    assert written["hash"] == approved_hash

    # The approval block is outside the hashed bytes: the hash is still the
    # plain snapshot hash of the same candidate.
    _, plain_hash = snapshot_hash(BREAKING, tmp_path, capsys, "plain.lock.json")
    assert written["hash"] == plain_hash

    # Nothing about the machine or the environment is recorded.
    lowered = text.lower()
    for marker in ("secret", "token", "password", "api_key", "apikey", "environ", "$"):
        assert marker not in lowered
    for value in (getpass.getuser(), socket.gethostname(), str(Path.home())):
        assert value and value not in text


def test_approve_writes_only_the_baseline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("tool_sentry.snapshot.approved_at", lambda: FROZEN_AT)
    lock, _ = snapshot_hash(BASELINE, tmp_path, capsys, "tools.lock.json")
    candidate = write_doc(tmp_path / "candidate.json", baseline_doc())
    before = candidate.read_text(encoding="utf-8")

    code, _, _ = run(
        ["approve", "--baseline", str(lock), "--candidate", str(candidate)], capsys
    )
    assert code == 0
    assert candidate.read_text(encoding="utf-8") == before
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "candidate.json",
        "tools.lock.json",
    ]


# --- policy and lock errors ------------------------------------------------


def test_malformed_policy_exits_2_with_one_error(tmp_path, capsys):
    policy = write_policy(tmp_path, "version: 1\nfail_on breaking")
    code, stdout, stderr = run(
        [
            "check",
            "--baseline",
            str(BASELINE),
            "--candidate",
            str(BREAKING),
            "--policy",
            str(policy),
        ],
        capsys,
    )
    assert code == 2
    assert stdout == ""
    assert stderr.startswith("tool-sentry: error:")
    assert stderr.count("tool-sentry: error:") == 1
    assert len(stderr.splitlines()) == 1


def test_stale_policy_version_exits_2(tmp_path, capsys):
    policy = write_policy(tmp_path, "version: 2\nfail_on: breaking")
    code, _, stderr = run(
        [
            "check",
            "--baseline",
            str(BASELINE),
            "--candidate",
            str(BREAKING),
            "--policy",
            str(policy),
        ],
        capsys,
    )
    assert code == 2
    assert "stale" in stderr
    assert "version" in stderr


@pytest.mark.parametrize("version", [0, 2])
def test_stale_baseline_lock_version_exits_2(version, tmp_path, capsys):
    policy = write_policy(tmp_path, "version: 1\nfail_on: breaking")
    lock, _ = snapshot_hash(BASELINE, tmp_path, capsys, "tools.lock.json")
    written = json.loads(lock.read_text(encoding="utf-8"))
    written["version"] = version
    lock.write_text(json.dumps(written, indent=2), encoding="utf-8")

    code, _, stderr = run(
        [
            "check",
            "--baseline",
            str(lock),
            "--candidate",
            str(BREAKING),
            "--policy",
            str(policy),
        ],
        capsys,
    )
    assert code == 2
    assert "stale lock format" in stderr
    assert str(version) in stderr


def test_duplicate_candidate_tool_names_exit_2(tmp_path, capsys):
    policy = write_policy(tmp_path, "version: 1\nfail_on: breaking")
    doc = baseline_doc()
    doc["tools"].append(json.loads(json.dumps(tool_of(doc, "create_ticket"))))
    candidate = write_doc(tmp_path / "dupes.json", doc)

    code, _, stderr = run(
        [
            "check",
            "--baseline",
            str(BASELINE),
            "--candidate",
            str(candidate),
            "--policy",
            str(policy),
        ],
        capsys,
    )
    assert code == 2
    assert "duplicate tool names in candidate" in stderr
    assert "create_ticket" in stderr


# --- report shape ----------------------------------------------------------


def test_check_json_report_key_order(tmp_path, capsys):
    policy = write_policy(tmp_path, "version: 1\nfail_on: breaking")
    code, report = check_report(
        ["--baseline", str(BASELINE), "--candidate", str(BREAKING), "--policy", str(policy)],
        capsys,
    )
    assert code == 1
    assert list(report) == [
        "baseline_hash",
        "candidate_hash",
        "counts",
        "ignored_count",
        "forbidden",
        "policy",
        "changes",
    ]
    assert report["baseline_hash"].startswith("sha256:")
    assert report["candidate_hash"].startswith("sha256:")
    assert report["policy"] == {"fail_on": "breaking", "path": str(policy)}
    assert sum(report["counts"].values()) == len(report["changes"])


def test_check_json_flag_is_an_alias_for_format_json(tmp_path, capsys):
    policy = write_policy(tmp_path, "version: 1\nfail_on: breaking")
    args = ["--baseline", str(BASELINE), "--candidate", str(BREAKING), "--policy", str(policy)]
    aliased_code, aliased, _ = run(["check", *args, "--json"], capsys)
    explicit_code, explicit, _ = run(["check", *args, "--format", "json"], capsys)
    assert aliased_code == explicit_code == 1
    assert aliased == explicit
