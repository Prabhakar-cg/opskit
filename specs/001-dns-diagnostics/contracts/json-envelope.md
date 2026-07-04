# Contract: `--json` / `--jsonl` output envelope

Every command emits a **versioned envelope**. Single target ⇒ one object. Batch with `--json` ⇒ a
top-level array of envelopes. `--jsonl` ⇒ one envelope per line (NDJSON). Schema changes are
governed by SemVer (Art. V); the current `schema_version` is `"1"`.

## Envelope shape

```json
{
  "schema_version": "1",
  "command": "dns.lookup",
  "query": {
    "target": "example.com",
    "record_types": ["A", "MX"],
    "servers": ["1.1.1.1"],
    "transport": "auto",
    "timeout_s": 5.0,
    "retries": 2,
    "port": 53
  },
  "result": {
    "outcome": "ok",
    "resolver": "1.1.1.1",
    "records": [
      { "type": "A", "value": "93.184.216.34", "ttl": 300 }
    ],
    "trace": null
  },
  "error": null,
  "elapsed_ms": 12.3
}
```

## Fields

| Field | Type | Notes |
|-------|------|-------|
| `schema_version` | string | envelope version (`"1"`) |
| `command` | string | e.g. `dns.lookup`, `dns.reverse`, `dns.compare` |
| `query` | object | echo of the resolved request parameters |
| `result` | object \| null | present on success/partial; `outcome`, `resolver`, `records[]`, optional `trace[]` |
| `error` | object \| null | `{ "code", "message", "hint" }` when the outcome is a failure |
| `elapsed_ms` | number | measured latency |

`compare` results carry a `result.comparison` with per-resolver results and a `consistent` boolean.

## Rules

- On failure, `result` may be `null` and `error` is populated; the top-level structure is unchanged.
- Field additions are backward-compatible; removals/renames/type-changes are breaking (MAJOR).
- Timestamps (if any) are ISO-8601 UTC; TTLs are integers; timings are floats (ms).
- A JSON Schema for this envelope ships in the package and is validated in `tests/contract/`.
