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
