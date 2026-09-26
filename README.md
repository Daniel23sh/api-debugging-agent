# APILens

APILens is a focused API Debugging Agent that will investigate bugs in a controlled REST API using bounded tools and evidence-backed diagnosis.

## Local setup

APILens requires Python 3.11 or newer. Create and activate a virtual environment, then install the project with development dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and set values as needed, including your OpenAI
API key. Tracing exports to Phoenix over OTLP/HTTP when
`PHOENIX_COLLECTOR_ENDPOINT` is set; leave it unset to disable trace export.

Run the Sandbox API and Agent Service in separate terminals:

```bash
uvicorn sandbox_api.main:app --env-file .env --port 8000
uvicorn agent_service.main:app --env-file .env --port 8001
```

Send a debugging request to the Agent Service:

```bash
curl http://127.0.0.1:8001/debug \
  -H 'Content-Type: application/json' \
  -d '{"issue":"POST /orders returns 500 for product 4"}'
```

## Phoenix trace verification

Tracing is enabled when `PHOENIX_COLLECTOR_ENDPOINT` is set (the example value
is `http://127.0.0.1:6006`) and disabled when it is unset or blank. To inspect a
local trace, start Phoenix first:

```bash
uvx arize-phoenix serve
```

Then start the Sandbox API and Agent Service with the commands above. The Agent
Service needs a configured `OPENAI_API_KEY`; restart it after changing tracing
configuration. Open [Phoenix](http://127.0.0.1:6006), select the `apilens`
project, and open the trace whose root is `DebugSession`.

Use this missing-product report for a short trajectory:

```bash
curl http://127.0.0.1:8001/debug \
  -H 'Content-Type: application/json' \
  -d '{"issue":"GET /products/999 returns 200 with an empty object, but a missing product should return 404"}'
```

Check that there is one `DebugSession`, each `agent_decision` contains its LLM
request, any selected TOOL execution is a sibling of the decision, and
`final_diagnosis` is a direct child of the session.

Use the seeded missing-inventory failure for a multi-step trajectory:

```bash
curl http://127.0.0.1:8001/debug \
  -H 'Content-Type: application/json' \
  -d '{"issue":"POST /orders returns 500 for product 4 with quantity 1. Investigate the contract, reproduce it, correlate the request ID with server logs, and inspect the implementation."}'
```

The exact tool order is model-driven. Check for several decisions with nested
LLM spans, TOOL siblings under the same `DebugSession`, request-ID metadata when
logs are inspected, a non-error `execute_api_request` TOOL span carrying HTTP
500 as evidence, and one `final_diagnosis`. There should be one LLM span per
actual model request, with no raw issue, model input/output, request/response
body, log message, or source code in span attributes.

The expected high-level shape is:

```text
DebugSession
├── agent_decision
│   └── LLM
├── approved tool name (TOOL)
├── ...
└── final_diagnosis
```

## Tests

```bash
pytest
ruff check .
```
