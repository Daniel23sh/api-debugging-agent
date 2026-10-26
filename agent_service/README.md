# Agent Service: local setup and API contract

For the project overview and Docker/frontend quick start, see the
[main README](../README.md). Run commands below from the repository root.
Live debugging uses OpenAI and may incur API costs; installation, startup and
offline tests do not make model requests. Keep credentials in the backend only.

## Run without Docker

Use Python 3.11 or newer; the constrained environment and CI are verified with
Python 3.13. The recorded local development interpreter is CPython 3.13.11.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -c constraints-dev.txt -e ".[dev]"
cp .env.example .env  # First setup only.
```

Run these in separate terminals from the repository root, with the same `.env`
and database path:

```bash
uvicorn sandbox_api.main:app --env-file .env --port 8000
uvicorn agent_service.main:app --env-file .env --port 8001
```

For host tracing, start Phoenix first (for example,
`uvx arize-phoenix==20.23.0 serve`) and retain
`PHOENIX_COLLECTOR_ENDPOINT=http://127.0.0.1:6006` in `.env`.
Otherwise set it blank to disable export. Restart the Agent after configuration
changes. Stop conflicting Docker services before using the same host ports.

`POST /debug` accepts a non-empty issue of up to 4,000 characters after trimming
outer whitespace. Longer issues receive HTTP 422 before provider construction;
reports are never silently truncated. This is a text limit, not an HTTP body-size
limit.

A **paid live example**, once credentials are configured:

```bash
curl http://127.0.0.1:8001/debug \
  -H 'Content-Type: application/json' \
  -d '{"issue":"POST /orders returns 500 for product 4 with quantity 1"}'
```

## Local demo API

`POST /demo/debug` runs the same provider, bounded loop, tools, and tracing as
`/debug`, returning
`{ "diagnosis": DebugDiagnosis, "steps": InvestigationStep[], "trace_id": string | null, "metrics": DemoExecutionMetrics | null }`
after completion. The optional one-page client consumes this endpoint.
Keep the service on localhost, as in Compose.
**This is a live, potentially paid OpenAI request.** Configure `OPENAI_API_KEY`
on the Agent Service. Startup and offline tests never request a model response.

The request requires `issue`, with the same whitespace trimming and 4,000-character
limit as `/debug`, and accepts an optional `scenario_id` from this table:

| `scenario_id` | Reproduction to describe in the issue |
| --- | --- |
| `missing-inventory` | `POST /orders` with product 4, quantity 1 returns HTTP 500 |
| `wrong-status` | `GET /products/999` returns HTTP 200 instead of 404 |
| `incorrect-total` | `POST /orders` with product 2, quantity 2 returns total `129.50` |
| `response-schema` | `GET /orders/1` returns `productId` instead of `product_id` |

For example, once credentials are configured:

```bash
curl http://127.0.0.1:8001/demo/debug \
  -H 'Content-Type: application/json' \
  -d '{"issue":"GET /products/999 returns 200 instead of 404","scenario_id":"wrong-status"}'
```

Every demo request gets a fresh, temporary SQLite file with the standard Sandbox
fixtures and an **in-process Sandbox application reached through ASGI transport**.
It does not use the Compose Sandbox network or its persistent database/logs.
POST/PATCH calls and request-ID log correlation use only this disposable database;
concurrent requests have separate files. Files are removed on completion, failure,
or cancellation. `response-schema` additionally prepares order 1 (product 1,
quantity 1) before the agent starts. Preparation is absent from the timeline.
The other scenarios need only default fixtures. Omitting `scenario_id` permits
free-form reports against those same fresh fixtures. Scenario selection never
changes the issue, forces tool choices, or guarantees `RESOLVED`.

Each step has `kind` (`tool_attempt`, `rejected_action`, or `session_error`),
an approved `tool_name` or null, optional approved `method` and normalized `route`,
`success`, optional HTTP `status_code`, canonical UUID `request_id`, allowlisted
`error_category`, and `retry`. Unavailable metadata is null; `retry` is true only
for an actual timeout retry. Array order is execution order. A successful HTTP
tool can observe HTTP 500; a rejected action was never executed. An early final
answer can return `steps: []`. The diagnosis is separate; no final tool or timing
is invented. For example, a step's sanitized shape is:

```json
{
  "kind": "tool_attempt", "tool_name": "execute_api_request",
  "method": "GET", "route": "/products/{product_id}", "success": true,
  "status_code": 200, "request_id": "6c0df0f0-0a17-47f8-858e-8a92f3762c65",
  "error_category": null, "retry": false
}
```

This is an illustrative shape, not a recorded result. The memory-only recorder
exports no arguments, bodies, source, log messages, model output, or reasoning.
It does not read evaluation artifacts or Phoenix storage. The existing diagnosis
contract supplies the final explanatory text; application-generated `LIMIT_REACHED`
responses omit raw fallback evidence and retain the limit reason and safe steps.
Missing credentials return a safe HTTP 503; provider/setup/execution failures return
a generic HTTP 502; invalid requests return HTTP 422 before model construction.
Typed tool failures and limits remain recorded outcomes. `/debug` continues to
return only `DebugDiagnosis` and uses the configured Sandbox/database as before.

`trace_id` is the active recording `DebugSession` root's OpenTelemetry trace ID:
32 lowercase hexadecimal characters, never all zero, or null when tracing is
disabled/nonrecording. It is correlation metadata, not authorization or proof of
export. The frontend validates the ID and constructs only the trusted local URL
`http://127.0.0.1:6006/redirects/traces/{trace_id}`. Phoenix 20.23.0 resolves the
project itself; the demo performs no project lookup and exposes no trace payloads.

The memory-only recorder also returns `metrics` with these definitions. A missing
metrics object (for example, an older service) is displayed as unavailable.

| Field | Measurement |
| --- | --- |
| `duration_seconds` | Monotonic elapsed time between agent-loop start/finish hooks, including decisions, tool executions and retries; excludes fixture preparation, provider construction, final-diagnosis span creation, export, and browser/network time. Null before lifecycle timing is available. |
| `tool_attempt_count` | Actual tool-start events, including failed attempts and retries; excludes rejected actions. Zero means no tool attempts. |
| `model_request_count` | Observed model-start hooks, including corrective requests, failures and unfinished calls; never inferred from decisions. Null when no hooks were observed, since scripted/uninstrumented providers may make no usage events. |
| `input_tokens`, `output_tokens`, `total_tokens` | Each field is summed only when **every** observed model request has a valid, nonnegative integer measurement for it. Missing, unfinished, or invalid usage makes that field null; no inferred totals or partial sums. An observed zero remains zero. |

Metrics come directly from execution hooks without querying Phoenix. Model names,
error messages and payloads are excluded; no dollar estimate is calculated.

The frontend displays this completed JSON after an explicit live-request and cost
acknowledgment. Completed responses may be retained in browser-local recent history;
the backend retains no demo session history. There is no streaming, reset endpoint,
or additional backend service.

## Related guides

- [Docker operations and connection troubleshooting](../DOCKER.md).
- [Frontend workflow and browser-local history](../README.md#demo-frontend).
- [Tracing, metrics and implementation walkthrough](../assets/demo/WALKTHROUGH.md).
- [Offline verification and evaluation](../evaluation/README.md).
