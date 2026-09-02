# tool-sentry

Tool definitions are an API contract, but they are usually pasted between an
OpenAI `tools` array, an Anthropic `tools` block, and an MCP server's
`tools/list` response, where the same contract looks different in each place
and drifts without anyone noticing. tool-sentry loads all three shapes from
JSON you already have on disk, normalizes them into one internal
representation, canonicalizes key order and description whitespace, and
writes a deterministic lock file with a SHA-256 contract hash. Commit the
lock file and any change to the tool surface shows up as a hash change in
the diff.

**tool-sentry never executes a model or a tool. It never calls provider
APIs.** It reads JSON files, canonicalizes them, and writes JSON. There are
no provider SDKs, no MCP transport, and no runtime dependencies.

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
tool-sentry diff BASELINE CANDIDATE [--fail-on SEVERITY] [--format FORMAT]
```

See [Diff](#diff) below.

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
tool-sentry diff BASELINE CANDIDATE [--fail-on SEVERITY] [--format FORMAT]
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

## Library use

```python
from tool_sentry import build_snapshot, load_tools

tools = load_tools(json.loads(text))   # auto-detects the dialect
snapshot = build_snapshot(tools)
print(snapshot["hash"])
```

## Development

```bash
python3 -m pip install -e ".[dev]"
make test
```

Python 3.11+. Zero runtime dependencies; `pytest` is the only dev
dependency. CI runs checkout, setup-python 3.12, install, and pytest — no
secrets and no tokens.

## License

MIT. Copyright (c) 2026 Justin Wilson.
