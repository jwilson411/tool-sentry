"""SARIF 2.1.0 rendering, and GitHub workflow-command annotations.

:func:`render_sarif` turns a list of :class:`~tool_sentry.classify.Change`
records into a SARIF 2.1.0 log so that a contract diff can be uploaded to a
code scanning service, kept as a build artifact, or read by any SARIF
viewer. :func:`render_annotations` turns that same log back into the
``::error`` / ``::warning`` / ``::notice`` workflow commands GitHub renders
as inline annotations, which is what the bundled composite action prints.

Mapping
-------
======================  ===============
tool-sentry severity     SARIF ``level``
======================  ===============
``breaking``             ``error``
``risky``                ``warning``
``informational``        ``note``
======================  ===============

The classifier rule id (``tool.removed``, ``enum.narrowed``, ...) becomes
the SARIF ``ruleId``, and the driver's ``rules`` array is derived from the
rule ids actually present in the run. The tool name and the dotted change
path are carried twice on purpose: as
``logicalLocations[0].fullyQualifiedName`` for viewers that navigate by
location, and as ``properties.tool`` / ``properties.path`` for consumers
that read the raw JSON.

Nothing here executes a model or a tool, and no network call is made: SARIF
is one more renderer over the same in-memory change list the table and the
JSON report use.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .classify import Change

#: The SARIF version this renderer emits.
SARIF_VERSION = "2.1.0"

#: The published SARIF 2.1.0 JSON Schema.
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"

DRIVER_NAME = "tool-sentry"
INFORMATION_URI = "https://github.com/jwilson411/tool-sentry"
RULES_URI = f"{INFORMATION_URI}#rules"

#: tool-sentry severity to SARIF result level.
SARIF_LEVELS: dict[str, str] = {
    "breaking": "error",
    "risky": "warning",
    "informational": "note",
}

#: SARIF result level to GitHub workflow command.
WORKFLOW_COMMANDS: dict[str, str] = {
    "error": "error",
    "warning": "warning",
    "note": "notice",
}

#: ``fullyQualifiedName`` for a change that belongs to the roster as a
#: whole rather than to one tool, such as ``roster.reordered``.
ROSTER_LOCATION = "contract"


def sarif_level(severity: str) -> str:
    """Return the SARIF level for a tool-sentry severity."""
    return SARIF_LEVELS.get(severity, "warning")


def _rule_name(rule: str) -> str:
    """Return the PascalCase SARIF rule name for a dotted rule id."""
    return "".join(part[:1].upper() + part[1:] for part in rule.split("."))


def qualified_name(tool: str, path: str) -> str:
    """Return the dotted location of a change, e.g. ``tool.parameters.x``."""
    parts = [part for part in (tool, path) if part]
    return ".".join(parts) if parts else ROSTER_LOCATION


def _location(change: Change) -> dict[str, Any]:
    fully_qualified = qualified_name(change.tool, change.path)
    name = fully_qualified.rsplit(".", 1)[-1]
    return {"name": name, "fullyQualifiedName": fully_qualified}


def _result(change: Change) -> dict[str, Any]:
    return {
        "ruleId": change.rule,
        "level": sarif_level(change.severity),
        "message": {"text": change.message},
        "logicalLocations": [_location(change)],
        "properties": {
            "severity": change.severity,
            "tool": change.tool,
            "path": change.path,
        },
    }


def _rules(changes: Sequence[Change]) -> list[dict[str, Any]]:
    """Derive the driver rule table from the rule ids present in the run.

    Changes arrive most severe first, so the first sighting of a rule id
    carries the severity used for its ``defaultConfiguration``.
    """
    seen: dict[str, str] = {}
    for change in changes:
        seen.setdefault(change.rule, change.severity)
    return [
        {
            "id": rule,
            "name": _rule_name(rule),
            "shortDescription": {
                "text": f"{rule}: a tool contract change tool-sentry classifies {severity}."
            },
            "defaultConfiguration": {"level": sarif_level(severity)},
            "helpUri": RULES_URI,
            "properties": {"severity": severity},
        }
        for rule, severity in sorted(seen.items())
    ]


def render_sarif(
    changes: Sequence[Change],
    *,
    tool_version: str,
    baseline_hash: str | None = None,
    candidate_hash: str | None = None,
) -> str:
    """Render classified changes as a pretty-printed SARIF 2.1.0 log.

    The log has exactly one run. ``baseline_hash`` and ``candidate_hash``,
    when given, are recorded on ``runs[0].properties`` so a stored SARIF
    file still says which two contracts produced it.
    """
    run: dict[str, Any] = {
        "tool": {
            "driver": {
                "name": DRIVER_NAME,
                "version": tool_version,
                "informationUri": INFORMATION_URI,
                "rules": _rules(changes),
            }
        },
        "results": [_result(change) for change in changes],
    }
    properties = {
        key: value
        for key, value in (
            ("baselineHash", baseline_hash),
            ("candidateHash", candidate_hash),
        )
        if value is not None
    }
    if properties:
        run["properties"] = properties
    document = {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [run],
    }
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False)


# --- GitHub workflow commands ----------------------------------------------


def _escape_data(text: str) -> str:
    """Escape a workflow command message."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(text: str) -> str:
    """Escape a workflow command property value."""
    return _escape_data(text).replace(":", "%3A").replace(",", "%2C")


def _annotation(result: dict[str, Any], *, file: str) -> str:
    rule = str(result.get("ruleId", "tool-sentry"))
    command = WORKFLOW_COMMANDS.get(str(result.get("level", "warning")), "warning")
    properties = result.get("properties") or {}
    message = (result.get("message") or {}).get("text", "")
    locations = result.get("logicalLocations") or [{}]
    where = locations[0].get("fullyQualifiedName") or ROSTER_LOCATION
    severity = properties.get("severity", "")
    title = f"{rule} ({severity})" if severity else rule
    return (
        f"::{command} file={_escape_property(file)},"
        f"title={_escape_property(title)}"
        f"::{_escape_data(f'{where}: {message}')}"
    )


def render_annotations(document: dict[str, Any], *, file: str) -> list[str]:
    """Return one GitHub workflow command per result in a SARIF log.

    ``file`` is the path GitHub anchors the annotation to, which for a
    contract check is the candidate document being proposed.
    """
    lines: list[str] = []
    for run in document.get("runs") or []:
        for result in run.get("results") or []:
            lines.append(_annotation(result, file=file))
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    """Print GitHub annotations for an existing SARIF file.

    ``python -m tool_sentry.sarif tool-sentry.sarif --file candidate.json``
    is what the bundled composite action runs after writing the log.
    """
    parser = argparse.ArgumentParser(
        prog="python -m tool_sentry.sarif",
        description=(
            "Print GitHub workflow-command annotations for a SARIF log "
            "written by 'tool-sentry --format sarif'."
        ),
    )
    parser.add_argument("sarif", metavar="SARIF", help="SARIF 2.1.0 log file")
    parser.add_argument(
        "--file",
        required=True,
        help="path the annotations are anchored to (the candidate document)",
    )
    args = parser.parse_args(argv)
    path = Path(args.sarif)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"tool-sentry: error: cannot read {path}: {exc}", file=sys.stderr)
        return 2
    if not isinstance(document, dict):
        print(f"tool-sentry: error: {path} is not a SARIF log", file=sys.stderr)
        return 2
    for line in render_annotations(document, file=args.file):
        print(line)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
