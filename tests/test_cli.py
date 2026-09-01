from __future__ import annotations

import hashlib
import json
import shutil

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
