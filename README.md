# tool-sentry

Tool definitions are an API contract, but they are usually pasted between an
OpenAI `tools` array, an Anthropic `tools` block, and an MCP server's
`tools/list` response, where the same contract looks different in each place
and drifts without anyone noticing. tool-sentry loads all three shapes from
JSON you already have on disk, normalizes them into one internal
representation, canonicalizes key order and description whitespace, and
writes a deterministic lock file with a SHA-256 contract hash. Commit the
lock file and any change to the tool surface shows up as a hash change in
the diff. `diff` then classifies each change as breaking, risky, or
informational, `check` judges those changes against a review policy you
commit next to the lock file, and `approve` records the new contract as the
approved baseline. Any of those reports can be written as a SARIF 2.1.0 log
with `--format sarif`, and the [reusable GitHub Action](#github-action)
bundled in this repository runs the whole check on a pull request and
annotates the candidate file inline.

**tool-sentry never executes a model or a tool. It never calls provider
APIs.** It reads JSON files, canonicalizes them, and writes JSON. There are
no provider SDKs, no MCP transport, and no runtime dependencies. The Action
needs no API key and no GitHub token.

## 60-second local example

```bash
git clone https://github.com/jwilson411/tool-sentry
cd tool-sentry
python3 -m pip install -e ".[dev]"

tool-sentry snapshot tests/fixtures/openai_tools.json --out tools.lock.json
```

Output:

```
sha256:a8b8738669594bc0da9dbee83ec3d442461509734db333befdb49eecdd24a936
```

The same two-tool contract is included as an Anthropic tools file and as an
MCP `tools/list` result. Both produce that same hash:

```bash
tool-sentry snapshot tests/fixtures/anthropic_tools.json --out /tmp/a.lock.json
tool-sentry snapshot tests/fixtures/mcp_tools_list.json  --out /tmp/m.lock.json
```

Run the tests with `make test`.

## CLI

```
tool-sentry snapshot INPUT [--out PATH]
```

- `INPUT` is a JSON file, or a directory containing one or more `.json`
  files. Directory contents are read in sorted filename order and the tools
  are concatenated in that order, then in each file's original order.
- `--out` defaults to `tools.lock.json` in the current directory.
- The contract hash is printed to stdout. Exit code is `0` on success.
- If the input format cannot be detected, or the file is missing or is not
  valid JSON, tool-sentry writes an error to stderr and exits `2`.

```
tool-sentry diff BASELINE CANDIDATE [--fail-on SEVERITY] [--format table|json|sarif]
```

See [Diff](#diff) below.

```
tool-sentry check --baseline PATH --candidate PATH --policy PATH [--format table|json|sarif]
tool-sentry approve --baseline PATH --candidate PATH
```

See [Policy and approval](#policy-and-approval) below.

Format detection, in order:

| Input shape | Detected as |
| --- | --- |
| List of `{"type": "function", "function": {...}}` | openai |
| Object with a `tools` list whose items have `inputSchema` | mcp |
| List (or `tools` list) whose items have `input_schema` | anthropic |
| Object with a `tools` list of OpenAI-style items | openai |

A JSON-RPC envelope (`{"jsonrpc": ..., "result": {"tools": [...]}}`) is
unwrapped as MCP.

## Internal representation

A loaded document is a list of tools. Each tool is:

```json
{
  "name": "search_documents",
  "description": "Search the indexed document corpus. Returns ranked matches.",
  "parameters": { "type": "object", "properties": {} },
  "source": "openai"
}
```

- `name` — the tool name, verbatim.
- `description` — whitespace-canonicalized.
- `parameters` — the JSON Schema for the tool's arguments, taken from
  `function.parameters` (OpenAI), `input_schema` (Anthropic), or
  `inputSchema` (MCP). A missing schema becomes
  `{"type": "object", "properties": {}}`.
- `source` — `"openai"`, `"anthropic"`, or `"mcp"`. This is provenance
  metadata only. It is **not** part of the hashed contract, which is what
  lets the same contract hash identically across the three dialects.

Tools keep the order they appear in the input. They are deliberately not
sorted by name: roster order is part of the contract, and sorting would hide
drift when a tool is added, removed, or moved.

## Canonicalization

1. Recurse through every object and array.
2. Sort object keys lexicographically at every object.
3. For description-like fields (`description`, `title`) whose value is a
   string, collapse runs of whitespace — including newlines and tabs — to a
   single space and strip the ends. This applies to the tool description and
   to nested schema `description` fields.
4. Never sort arrays. `required`, `enum`, and `prefixItems` are
   semantically ordered, so reordering them is a real contract change and
   changes the hash. That is intentional.

## Lock file and hash algorithm

```json
{
  "version": 1,
  "hash": "sha256:<hex>",
  "tools": [ { "description": "...", "name": "...", "parameters": {} } ]
}
```

The hash covers **only the `tools` array**, and only the
`{description, name, parameters}` keys of each tool:

```python
payload = json.dumps(tools, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
digest = "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

The on-disk file is pretty-printed with two-space indent for readable diffs.
Because the hashed byte string is the compact canonical JSON of `tools`
alone, that formatting — and the `version` and `hash` keys themselves —
cannot affect the hash.

Same hash: shuffled object key order, reflowed or re-indented descriptions,
a bare list versus a `{"tools": [...]}` wrapper, and the same contract
expressed as OpenAI, Anthropic, or MCP.

Different hash: a renamed tool or property, a changed type, an added or
removed `required` key, a reordered `required` or `enum` list, a changed
description, an added constraint, a dropped tool, or a reordered roster.

## Diff

A hash change tells you the contract moved. `diff` tells you whether that
matters.

```
tool-sentry diff BASELINE CANDIDATE [--fail-on SEVERITY] [--format table|json|sarif]
```

`BASELINE` and `CANDIDATE` are each a lock file, a dialect JSON file, or a
directory of `.json` files — in any combination. A committed lock file can be
compared straight against a freshly fetched `tools/list` response, or an
OpenAI tools file against the Anthropic copy of the same contract.

```bash
tool-sentry diff tests/fixtures/classify/baseline.json \
                 tests/fixtures/classify/candidate_breaking.json
```

```
SEVERITY       RULE                  TOOL              PATH                              MESSAGE
breaking       tool.removed          create_ticket                                       tool 'create_ticket' was removed
breaking       arg.required.added    search_documents  parameters.required.limit         property 'limit' became required
breaking       enum.narrowed         search_documents  parameters.properties.order.enum  allowed values were removed
breaking       output.field.removed  search_documents  output.properties.total           output field 'total' was removed
informational  arg.optional.added    search_documents  parameters.properties.offset      optional property 'offset' was added
```

When nothing changed, the table is exactly `No contract changes.`

Tools are matched by **name**, not by position, so a reordered roster is
reported once as `roster.reordered` rather than as a wall of unrelated edits.
Rows are sorted by severity, then tool, then rule, then path, so the output
is stable enough to diff or snapshot in CI.

### Severities

| Severity | Meaning |
| --- | --- |
| `breaking` | A call that was valid against the baseline can now fail, or a consumer that read the old output can now break. |
| `risky` | A real contract delta that is neither obviously safe nor obviously breaking. This is the default bucket for schema changes. |
| `informational` | Documentation and additive changes that leave every previously valid call valid. |

### `--fail-on`

Exit `1` when any change reaches the given severity, otherwise exit `0`.
Default is `breaking`. Unreadable input still exits `2`.

| `--fail-on` | Exits 1 when the diff contains |
| --- | --- |
| `breaking` (default) | a breaking change |
| `risky` | a risky **or** breaking change |
| `informational` | any change at all |
| `never` | nothing — always exits `0` |

The table or report is printed either way, so `--fail-on never` is the way to
report drift without failing the build.

### `--format`

`table` (default) is the aligned text above. `--format json` (or `--json`)
prints a report with exactly these top-level keys:

```json
{
  "baseline_hash": "sha256:...",
  "candidate_hash": "sha256:...",
  "counts": { "breaking": 4, "risky": 0, "informational": 1 },
  "changes": [
    {
      "severity": "breaking",
      "rule": "enum.narrowed",
      "tool": "search_documents",
      "path": "parameters.properties.order.enum",
      "message": "allowed values were removed",
      "before": ["relevance", "recency", "title"],
      "after": ["relevance", "recency"]
    }
  ]
}
```

`baseline_hash` and `candidate_hash` are the same contract hashes `snapshot`
prints.

`--format sarif` prints a [SARIF 2.1.0](https://sarifweb.azurewebsites.net/)
log instead, so a contract diff can be uploaded to a code scanning service,
kept as a build artifact, or opened in any SARIF viewer:

```json
{
  "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
  "version": "2.1.0",
  "runs": [
    {
      "tool": {
        "driver": { "name": "tool-sentry", "version": "0.4.0", "rules": [] }
      },
      "results": [
        {
          "ruleId": "enum.narrowed",
          "level": "error",
          "message": { "text": "allowed values were removed" },
          "logicalLocations": [
            {
              "name": "enum",
              "fullyQualifiedName": "search_documents.parameters.properties.order.enum"
            }
          ],
          "properties": {
            "severity": "breaking",
            "tool": "search_documents",
            "path": "parameters.properties.order.enum"
          }
        }
      ],
      "properties": { "baselineHash": "sha256:...", "candidateHash": "sha256:..." }
    }
  ]
}
```

There is exactly one run. The classifier rule id is the SARIF `ruleId`, and
`runs[0].tool.driver.rules` lists every rule id present in that run, each
with its default level and a link to the [rule table](#rules). The tool name
and the dotted change path are carried twice on purpose: as
`logicalLocations[0].fullyQualifiedName` for viewers that navigate by
location, and as `properties.tool` / `properties.path` for consumers that
read the raw JSON. A change that belongs to the roster rather than to one
tool, such as `roster.reordered`, is located at `contract`.

Severity becomes the SARIF `level`:

| Severity | SARIF `level` |
| --- | --- |
| `breaking` | `error` |
| `risky` | `warning` |
| `informational` | `note` |

SARIF is a renderer, not a mode: `--fail-on` and the exit codes behave
exactly as they do for `table` and `json`. A clean diff is a valid log with
an empty `results` array.

### Rules

| Rule | Severity | Fires when |
| --- | --- | --- |
| `tool.removed` | breaking | A tool in the baseline is not in the candidate. |
| `arg.removed` | breaking | An input property was removed. |
| `arg.required.added` | breaking | An input property became required, or a new required property appeared. |
| `enum.narrowed` | breaking | Allowed values were removed, or an `enum`/`const` was added where the value was previously unconstrained. |
| `const.changed` | breaking | A `const` was replaced with a different `const`. |
| `type.incompatible` | breaking | An input `type` no longer accepts a type it used to. |
| `output.field.removed` | breaking | An output property, or the whole output schema, was removed. |
| `output.type.incompatible` | breaking | An output `type` no longer accepts a type it used to. |
| `arg.required.removed` | risky | An input property is no longer required. |
| `enum.widened` | risky | Allowed values were added, or an `enum`/`const` was dropped. |
| `type.widened` | risky | A `type` gained members without losing any — including `integer` → `number`, which keeps every old call valid but changes what the tool may return and what other producers may send. |
| `constraint.tightened` | risky | A `minimum`/`maximum`/`minLength`/`maxLength`/`minItems`/`maxItems`/`exclusive*` bound got stricter or was added. |
| `constraint.loosened` | risky | One of those bounds got looser or was removed. |
| `additionalProperties.changed` | risky | `additionalProperties` changed as a value (`true`/`false`/schema-vs-boolean). |
| `union.changed` | risky | `anyOf`/`oneOf`/`allOf` gained, lost, or replaced a branch. Reordering the same branches is not a change. |
| `format.changed` | risky | `format` was added, removed, or changed. |
| `default.changed` | risky | `default` was added, removed, or changed. |
| `schema.changed` | risky | Any other structural delta, including an output field becoming required. This is the catch-all: unknown keywords land here rather than being ignored. |
| `tool.added` | informational | A tool in the candidate is not in the baseline. |
| `roster.reordered` | informational | The same tools appear in a different order. |
| `arg.optional.added` | informational | A new optional input property appeared. |
| `output.field.optional.added` | informational | A new output property appeared, or an output schema was added where there was none. |
| `enum.reordered` | informational | The allowed values are the same set in a different order. |
| `description.changed` | informational | A tool `description`, or a schema `description`/`title`, changed. |
| `examples.changed` | informational | `example`/`examples` changed. |

An output field becoming required is `risky`, not `breaking`: it does not
invalidate any call, but a producer that omitted the field no longer
conforms. An output field being **added** is informational, while an input
field being added is breaking only if it is required.

### What this is not

`diff` is a **deterministic structural classifier**, not a JSON Schema
semantic equivalence checker. It compares two canonicalized schema trees
keyword by keyword and applies the fixed rules above. Specifically:

- **`$ref` is not resolved.** A `$ref` is compared as a plain string value;
  changing what it points at, or inlining a `$ref` into its equivalent
  schema, is reported as a change (usually `schema.changed`) even though the
  two schemas may be semantically identical.
- Logically equivalent rewrites are reported as changes — `{"type":
  ["string", "null"]}` versus `{"anyOf": [{"type": "string"}, {"type":
  "null"}]}`, or a constraint moved between an `allOf` branch and its parent.
- `unevaluatedProperties`, `patternProperties`, `if`/`then`/`else`,
  `dependentSchemas` and other keywords without a dedicated rule are compared
  as values and land in `schema.changed`.
- Severities are heuristics about the common case, not proofs. `risky` in
  particular means "look at this," not "this breaks."

Nothing is executed and no model is asked. The classification of a given pair
of files is the same on every machine and every run.

## Policy and approval

`diff` tells you what changed and how bad it is. `check` decides whether
*this repository* accepts it, using a policy file you commit next to the
lock file, and `approve` records a reviewed contract as the new baseline.

```
tool-sentry check --baseline PATH --candidate PATH --policy PATH [--format table|json|sarif]
tool-sentry approve --baseline PATH --candidate PATH
```

### The policy file

The policy is YAML — or the same keys as JSON, which is read first if the
file parses as JSON. tool-sentry parses a small documented YAML subset
itself, so it still has **zero runtime dependencies**.

```yaml
version: 1
fail_on: risky
ignore_paths:
  - parameters.properties.offset
forbidden_tool_names:
  - "eval"
  - "shell_*"
tools:
  search_documents:
    fail_on: informational
    ignore_paths:
      - parameters.properties.order
```

| Key | Meaning |
| --- | --- |
| `version` | Required. Must be `1`. Any other value is a stale policy and exits `2` rather than being half-understood. |
| `fail_on` | Global threshold — `breaking` (default), `risky`, `informational`, or `never`, exactly as [`diff --fail-on`](#--fail-on). |
| `ignore_paths` | Change paths to drop before the threshold is applied. |
| `forbidden_tool_names` | Patterns that no candidate tool name may match. |
| `tools` | Per-tool overrides, keyed by tool name; each may set `fail_on` and `ignore_paths`. |

Every key except `version` is optional. An unknown top-level key, an unknown
per-tool key, or a bad value is one error and exit `2` — a typo'd policy is
never silently a permissive one.

The supported YAML is block mappings, block sequences of scalars, `#`
comments, quoted and unquoted scalars, and the empty collections `[]` and
`{}`. Anchors, aliases, tags, merge keys, block scalars, and multi-document
streams are rejected with one error rather than being partly read.

### `ignore_paths`

An ignore entry is matched against a change's `path` — the same dotted path
the table and the JSON report print — as a **path prefix**, on segment
boundaries. `parameters.properties.order` ignores itself and
`parameters.properties.order.enum`, but not `parameters.properties.ordering`.
Matching is exact and case-sensitive; there is no globbing here.

An ignored change is removed from the report entirely and counted in
`ignored_count`, so a change that would have failed the build becomes a
pass. Roster-level changes — `tool.added`, `tool.removed`,
`roster.reordered` — carry an empty path and can never be ignored.

### `forbidden_tool_names`

Each pattern is an `fnmatch` glob (`*`, `?`, `[seq]`) matched
**case-sensitively** against every **candidate** tool name — the roster
being proposed, not the baseline. `shell_*` matches `shell_exec` and not
`Shell_exec`. A match fails the run on its own, regardless of `fail_on` and
regardless of whether anything changed:

```bash
tool-sentry check --baseline tests/fixtures/classify/baseline.json \
                  --candidate tests/fixtures/classify/baseline.json \
                  --policy policy.yml   # forbidden_tool_names: ["create_*"]
```

```
No contract changes.
FORBIDDEN  PATTERN   NAME
FORBIDDEN  create_*  create_ticket
```

### Per-tool overrides

A tool's `fail_on` replaces the global one for changes on that tool, so one
volatile tool can be held to `informational` while the rest of the roster
stays at `breaking` — or one critical tool held to `risky` while the rest
stay looser. A tool's `ignore_paths` are added to the global ones and apply
only to changes on that tool: the same path under a different tool is still
reported.

### `check`

`--baseline` and `--candidate` accept a lock file, a dialect JSON file, or a
directory of `.json` files, as `diff` does. The baseline is additionally
required to be a lock format this release understands; a lock written with a
different `version` exits `2` and asks you to regenerate it. A roster that
names the same tool twice also exits `2` — `check` and `approve` match
rosters by name, so a repeated name has no single meaning.

```bash
tool-sentry check --baseline tests/fixtures/classify/baseline.json \
                  --candidate tests/fixtures/classify/candidate_breaking.json \
                  --policy policy.yml
```

```
SEVERITY  RULE                  TOOL              PATH                              MESSAGE
breaking  tool.removed          create_ticket                                       tool 'create_ticket' was removed
breaking  arg.required.added    search_documents  parameters.required.limit         property 'limit' became required
breaking  enum.narrowed         search_documents  parameters.properties.order.enum  allowed values were removed
breaking  output.field.removed  search_documents  output.properties.total           output field 'total' was removed
```

`--format json` (or `--json`) prints a report with exactly these top-level
keys:

```json
{
  "baseline_hash": "sha256:...",
  "candidate_hash": "sha256:...",
  "counts": { "breaking": 4, "risky": 0, "informational": 0 },
  "ignored_count": 1,
  "forbidden": [],
  "policy": { "fail_on": "risky", "path": "policy.yml" },
  "changes": [
    {
      "severity": "breaking",
      "rule": "enum.narrowed",
      "tool": "search_documents",
      "path": "parameters.properties.order.enum",
      "message": "allowed values were removed",
      "before": ["relevance", "recency", "title"],
      "after": ["relevance", "recency"]
    }
  ]
}
```

`changes` entries have the same keys as in the [`diff` report](#--format).
`forbidden` entries are `{"pattern": ..., "name": ...}` — the pattern that
matched and the candidate tool name it matched. `policy` echoes the global
`fail_on` and the policy path the run used.

`counts` and `changes` describe what survived `ignore_paths`; `ignored_count`
is how many changes it dropped.

`--format sarif` prints the surviving changes as the same
[SARIF 2.1.0 log](#--format) `diff` writes, so an ignored path is absent
from the log as well as from the report. A forbidden tool name is a policy
verdict rather than a contract change: it still fails the run and it is
still listed by `table` and `json`, but it has no SARIF result.

### `approve`

```bash
tool-sentry approve --baseline tools.lock.json \
                    --candidate tests/fixtures/classify/candidate_breaking.json
```

`approve` writes the candidate contract to `--baseline` as a lock file and
prints its hash. **It writes that one file and nothing else** — the
candidate is only read, no other path is touched, and nothing is staged or
committed. Running `check` again against that baseline now passes, because
there is nothing left to differ.

An approved baseline is an ordinary lock file with one extra block:

```json
{
  "version": 1,
  "hash": "sha256:...",
  "approved": { "tool_sentry": "0.3.0", "at": "2026-09-02T06:00:00Z" },
  "tools": []
}
```

`approved.tool_sentry` is the version that wrote it and `approved.at` is the
UTC approval time, second precision, with a trailing `Z`. No user name, host
name, environment, or credential is recorded — there is nothing to record,
since tool-sentry reads only the files you name.

The hash still covers **only the `tools` array**, exactly as in
[Lock file and hash algorithm](#lock-file-and-hash-algorithm). The
`approved` block sits outside the hashed byte string, so approving a
contract does not change its hash: an approved baseline and a plain
`snapshot` of the same candidate carry the same `sha256:`.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | The candidate is accepted under the policy. `approve` succeeded. |
| `1` | `check` only: a surviving change reached its threshold, or a candidate tool name is forbidden. |
| `2` | Bad input — unreadable or invalid JSON, an unrecognized document, a stale policy or lock version, an invalid policy, or duplicate tool names. One line on stderr, prefixed `tool-sentry: error:`. |

Exit `1` means "a human needs to look at this," and the report is printed
either way, so a reviewer sees the same table CI did.

### What this is not

Approval is a **local, git-reviewable record**, not an authorization system.
`approve` is a separate command you run deliberately: `check` never
auto-approves, never rewrites the baseline, and never widens the policy.
There is no server, no token, and no network — a policy is a file you read
in a diff, and an approval is a file you review in a pull request. As
everywhere else in tool-sentry, nothing is executed and no model is asked.

## GitHub Action

The repository root is a composite action, so a pull request can be checked
against a committed baseline with nothing but a checkout:

```yaml
name: tool-sentry
on: [pull_request]
jobs:
  contract:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: jwilson411/tool-sentry@main
        with:
          baseline: tools.lock.json
          candidate: tools.json
```

`baseline` is the lock file you commit and review; `candidate` is the tool
document the branch proposes. Both accept a lock file, a dialect JSON file,
or a directory of `.json` files, exactly as the CLI does. The step exits `1`
when the diff reaches the threshold, so the job fails the way any other
check does.

| Input | Default | Meaning |
| --- | --- | --- |
| `baseline` | required | Approved baseline contract. |
| `candidate` | required | Candidate contract the branch proposes. |
| `fail-on` | `breaking` | Severity that fails the run, as [`diff --fail-on`](#--fail-on). Ignored when `policy` is set. |
| `format` | `sarif` | What gets printed to the job log: `sarif`, `table`, or `json`. The SARIF file is written either way. |
| `policy` | none | Policy file. When set, [`check`](#check) runs instead of `diff` and the policy supplies the threshold. |
| `output` | `tool-sentry.sarif` | Path the SARIF 2.1.0 log is written to. |
| `python-version` | `3.12` | Python used to run tool-sentry. |

Outputs are `sarif-file` (the path written) and `exit-code` (the tool-sentry
exit code, before the step fails on it).

The action pins `actions/setup-python@v5` and installs tool-sentry from
`$GITHUB_ACTION_PATH`, the checked-out action itself, so there is no PyPI
dependency and the action always runs the code it ships with. **It needs no
API key, no provider credential, and no `GITHUB_TOKEN`**: it reads two files
and writes one. It never posts a pull request comment, never calls a
provider API, and never executes a model or a tool. The SARIF log is left on
disk for whatever the workflow wants to do with it, such as an artifact
upload, under the workflow's own permissions rather than the action's.

### Expected output

`examples/sarif/` holds a baseline and a candidate that fail on purpose. Run
the same two steps the action runs:

```bash
tool-sentry diff examples/sarif/baseline.json examples/sarif/candidate.json \
                 --format sarif > tool-sentry.sarif
python -m tool_sentry.sarif tool-sentry.sarif --file examples/sarif/candidate.json
```

The second command prints one GitHub workflow command per change, which the
runner renders as an inline annotation on the candidate file:

```
::error file=examples/sarif/candidate.json,title=arg.removed (breaking)::create_ticket.parameters.properties.title: property 'title' was removed
::error file=examples/sarif/candidate.json,title=arg.required.added (breaking)::create_ticket.parameters.properties.summary: required property 'summary' was added
::error file=examples/sarif/candidate.json,title=tool.removed (breaking)::export_report: tool 'export_report' was removed
::error file=examples/sarif/candidate.json,title=arg.required.added (breaking)::search_documents.parameters.required.limit: property 'limit' became required
::error file=examples/sarif/candidate.json,title=enum.narrowed (breaking)::search_documents.parameters.properties.order.enum: allowed values were removed
::error file=examples/sarif/candidate.json,title=output.field.removed (breaking)::search_documents.output.properties.total: output field 'total' was removed
::warning file=examples/sarif/candidate.json,title=enum.widened (risky)::create_ticket.parameters.properties.severity.enum: allowed values were added
::notice file=examples/sarif/candidate.json,title=arg.optional.added (informational)::search_documents.parameters.properties.offset: optional property 'offset' was added
```

Five distinct breaking rules in one candidate: a dropped tool, a removed
argument, an argument that became required, a narrowed enum, and a removed
output field. `::error` and `::warning` and `::notice` are the workflow
commands for the three severities, in the same order the table and the JSON
report use.

## Library use

```python
from tool_sentry import build_snapshot, load_tools

tools = load_tools(json.loads(text))   # auto-detects the dialect
snapshot = build_snapshot(tools)
print(snapshot["hash"])
```

The SARIF renderer lives in its own module, so that
`python -m tool_sentry.sarif` runs as a script:

```python
from tool_sentry import classify
from tool_sentry.sarif import render_sarif

print(render_sarif(classify(baseline, candidate), tool_version="0.4.0"))
```

## Development

```bash
python3 -m pip install -e ".[dev]"
make test
```

Python 3.11+. Zero runtime dependencies; `pytest` is the only dev
dependency. CI runs checkout, setup-python 3.12, install, and pytest, then
snapshots the committed sample contract and diffs the lock back against the
document it came from, which must come out clean. A second job runs the
bundled action on `examples/sarif/` to prove it works end to end. No
secrets and no tokens anywhere.

## License

MIT. Copyright (c) 2026 Justin Wilson.
