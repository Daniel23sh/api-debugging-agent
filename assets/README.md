# Project images and demo recordings

These assets document recorded behavior. The historical assets described below
were created without new agent or judge calls. Current, separately approved live
demo captures are in [demo/](demo/README.md). Raw snapshots and
Phoenix databases remain ignored.

The [25 October V3 verification](../evaluation/reports/v3-verification-2026-10-25.md)
separately records 17/18 strict successes on the full frozen dataset. The media
here retains its original dates and trace identities; no image or video represents
the V3 run.

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

## Current results overview

[`v1-v2-v3-results.svg`](v1-v2-v3-results.svg) is the main README's results chart,
using the same zero-to-18 scale and adding V3 = 17/18 (94.4%) from the
[separate V3 report](../evaluation/reports/v3-verification-2026-10-25.md).
The chart explicitly distinguishes the controlled V1/V2 experiment from V3's
current-system verification; it does not imply a third controlled arm or causal
attribution. It is a self-contained accessible SVG, not a captured interface.
The original V1/V2 measurements remain in the
[historical comparison report](../evaluation/reports/v1-v2-experiment-2026-10-21.md).
