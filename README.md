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
API key.

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

## Tests

```bash
pytest
ruff check .
```
