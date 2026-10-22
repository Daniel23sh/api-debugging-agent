# Portfolio evidence

These assets document recorded behavior. No agent, judge, or paid API request
was run to create them. Raw snapshots and Phoenix databases remain ignored.

## Architecture

The Mermaid diagram lives directly in the [main README](../README.md#architecture).
It follows the current FastAPI entry point, decision provider, bounded loop,
dispatch registry, HTTP tools, shared SQLite log lookup, allowlisted source
inspection, and app-owned tracing. Supporting evaluation/CI workflows are drawn
outside the three-service Compose runtime.

## Historical Phoenix screenshot

[`phoenix-missing-inventory.png`](phoenix-missing-inventory.png) is an unaltered
Phoenix 20.23.0 browser screenshot captured on 2026-10-22. It displays a historical
2026-10-21 V2 investigation, not a synthetic span or a new model run.

| Identity | Recorded value |
| --- | --- |
| Case | `order_missing_inventory_02` |
| Experiment | `d8c60628-26f8-4c6e-a415-d6d74d20d161` |
| Phoenix project | `apilens-v2-d8c60628-26f8-4c6e-a415-d6d74d20d161` |
| Trace | `618dde8c5db5cb7d9f7843f4aae7322e` |
| Session | `c25c8e92-11ff-4913-9344-91ff11544318` |
| Root span | `c76ad2aed84c41d6` |
| Selected HTTP span | `826cb8542044affd` |
| Correlated request | `f503978c-1b38-4857-bfd5-04046db73df2` |
| Tree | 16 spans: one root, five decisions, five LLM calls, four tools, one final diagnosis |

The [public comparison report](../evaluation/reports/v1-v2-experiment-2026-10-21.md#trace-lookup-references)
identifies this trace and describes the investigation. The retained private run
snapshot and SQLite trace tree were checked for matching session/root/trace IDs,
connected parent relationships, tool/decision/model-call counts, final `RESOLVED`
status, and the same request ID in HTTP and log-inspection spans. The saved outcome
is `CORRECT` / `GROUNDED` / strict success.

Capture procedure: copy the retained V2 Phoenix database and its WAL/SHM sidecars
to a temporary directory, open that copy with the pinned Phoenix image on
localhost:6007, select the exact trace and `execute_api_request` span, hide tree
metrics, and capture the browser viewport. Original database files were hashed
before and after; none changed. No spans, attributes, or historical results were
edited. The temporary viewer is separate from the normal Compose Phoenix volume.

The visible fields are tool names, IDs, timestamps, duration, method/path, HTTP
500, and tool-success metadata. No issue text, model output, request/response
payload, raw log message, source excerpt, credentials, or personal information is
displayed. LLM input/output attributes were checked as `__REDACTED__`. Some long
IDs are clipped by the UI; full reviewed lookup IDs are listed above. Phoenix's
`Unset` is an OpenTelemetry span status, not the final diagnosis status; its cost
display is a UI estimate, not a measured billing result or a cost-savings claim.

## Recorded experiment comparison

[`v1-v2-results.svg`](v1-v2-results.svg) is a newly authored vector chart of the
sanitized [2026-10-21 comparison report](../evaluation/reports/v1-v2-experiment-2026-10-21.md#results-and-denominators).
It is not a screenshot of Phoenix or a historical comparison interface.

The bars use a common zero-to-18 scale: V1 = 13/18 (72.2%), V2 = 15/18 (83.3%).
All 13 original strict successes were retained. Percentages are rounded to one
decimal place. Strict success requires expected status, full grounding, and
correct root cause where applicable; the insufficient-input control is included.
The README separately reports unchanged grounding counts, the remaining failures,
token reduction, and increased latency.

The SVG is self-contained text with accessible title/description, no scripts,
external images, fonts, or data requests. Values can be checked directly against
the report and the README table. One run per case per version supports an observed
comparison, not a statistical reliability estimate.
