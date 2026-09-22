# APILens

APILens is a focused API Debugging Agent that will investigate bugs in a controlled REST API using bounded tools and evidence-backed diagnosis.

## Local setup

APILens requires Python 3.11 or newer. Create and activate a virtual environment, then install the project with development dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and set values as needed.

Run the Sandbox API locally:

```bash
uvicorn sandbox_api.main:app --reload
```

## Tests

```bash
pytest
ruff check .
```
