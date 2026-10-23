# Docker local runtime

Requires a running Docker Engine / Docker Desktop with Compose v2. The stack uses
Python 3.13.11 and Phoenix 20.23.0. Stop host processes using ports 8000, 8001,
or 6006 before starting it.

```bash
cp .env.example .env  # First setup only; preserve an existing .env.
# For live debugging, edit .env and set OPENAI_API_KEY.
docker compose config --quiet
docker compose up --build
```

Subsequent starts can use `docker compose up`. For a detached start that waits
for readiness, use `docker compose up -d --wait`. No OpenAI key is required for
startup or the smoke checks below. Live `POST /debug` and `POST /demo/debug`
requests require valid OpenAI credentials and may incur API costs; startup and
health checks never call OpenAI. Only the Agent receives the key, through its
runtime environment.
Keep `.env` private and untracked. Use `docker compose config --quiet` when
validating a real `.env`: the full rendered config and container inspection can
reveal runtime secrets. No keys are passed as build arguments or copied into images.

| Service | Host address | Container address | Persistent storage |
| --- | --- | --- | --- |
| Sandbox API | http://127.0.0.1:8000/docs | `http://sandbox-api:8000` | `sandbox-data` at `/data`, database `/data/sandbox.db` |
| Agent Service | http://127.0.0.1:8001/docs | `http://agent-service:8001` | Same `sandbox-data` at `/data`, mounted read-only |
| Phoenix UI / OTLP HTTP | http://127.0.0.1:6006 | `http://phoenix:6006` | `phoenix-data` at `/home/nonroot` |

Compose creates a private service network with DNS names matching the services.
Uvicorn listens on `0.0.0.0` inside each container so peers can connect; published
ports bind to host loopback only. These unauthenticated development services are
intended for a trusted local machine. No gRPC or database port is published.

Compose overrides `SANDBOX_BASE_URL` and `SANDBOX_DATABASE_PATH` with the values
above. The Agent reproduces requests over HTTP; the Sandbox writes structured
logs with `X-Request-ID` to SQLite; `inspect_server_logs` reads that same file
directly. The entire database directory is shared so SQLite journals and locks
stay beside the database. SQLite suits this small local stack; run one Sandbox
worker and use local Docker volumes, not a network filesystem. Health checks use
the existing OpenAPI endpoints; the Agent waits for Sandbox initialization and
Phoenix database readiness. They do not require credentials or a new API route.

`POST /demo/debug` instead runs a disposable Sandbox inside the Agent process using
ASGI transport and a fresh SQLite file under the container's temporary directory.
It needs no write access to `/data` and never resets the shared Sandbox or its logs.
See the [demo API contract and scenarios](README.md#local-portfolio-demo-api).

## Portfolio frontend

Run `docker compose up --build -d --wait` from the repository root to start the
current backend code. Then use Node 22.12+ (Node 22 LTS) in a separate terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open [APILens](http://127.0.0.1:5173). Select a sample or enter a custom issue,
acknowledge the possible OpenAI cost, and start the investigation. Review the
returned tool timeline and diagnosis. Editing a sample switches to free-form
fixtures. Scenarios do not force tool choices or a resolved diagnosis.

Vite listens on loopback and proxies `/demo` to the Agent at `127.0.0.1:8001`.
The browser never contacts OpenAI or receives credentials. A missing key produces
HTTP 503; set `OPENAI_API_KEY` in the private backend `.env` and recreate the Agent
with `docker compose up -d agent-service`. Do not add frontend key variables.
If the backend is unavailable, verify `docker compose ps` and
`http://127.0.0.1:8001/openapi.json`; if `/demo/debug` returns 404, rebuild the older
Agent image. Port 5173 must be free; Vite intentionally fails instead of silently
switching ports. No live requests run during installation, startup, tests, or build.

The frontend runs outside Compose for this local workflow. Its production build
does not add a public server or change Docker images, ports, or persistent volumes.

## Tracing storage

Phoenix uses its default SQLite backend under
`PHOENIX_WORKING_DIR=/home/nonroot/.phoenix`. Mounting its existing non-root home
preserves the image's directory ownership on a fresh volume. Compose explicitly
runs Phoenix as that directory's owner, UID/GID 65532.
The Agent sets `PHOENIX_COLLECTOR_ENDPOINT=http://phoenix:6006`; the existing
OTLP/HTTP exporter appends `/v1/traces`. `PHOENIX_PROJECT_NAME` and
`OTEL_SERVICE_NAME` retain their `.env` settings. To disable Docker trace export,
set `DOCKER_PHOENIX_COLLECTOR_ENDPOINT=` in `.env` and recreate the Agent with
`docker compose up -d`. This separate override preserves the host-based tracing
address. Phoenix telemetry and external resource fetching are disabled. Existing
trace privacy filters remain in effect; credentials are not sent to Phoenix.

## Offline smoke checks

These checks make no model requests and need no external runtime service:

```bash
# Even with no .env or credentials, the stack can start:
OPENAI_API_KEY= docker compose --env-file /dev/null up --build -d --wait
curl --fail http://127.0.0.1:8000/products/1
curl --fail http://127.0.0.1:8001/openapi.json
curl --fail http://127.0.0.1:6006/readyz

# Exercise the actual tools inside the Agent container, including a seeded 500.
docker compose exec -T agent-service python - <<'PY'
import asyncio
import os
from agent_service.tools.execute_api_request import ExecuteApiRequestArgs, execute_api_request
from agent_service.tools.inspect_server_logs import InspectServerLogsArgs, inspect_server_logs
from agent_service.observability import configure_tracing, get_tracer, shutdown_tracing

assert os.environ['SANDBOX_BASE_URL'] == 'http://sandbox-api:8000'
assert os.environ['SANDBOX_DATABASE_PATH'] == '/data/sandbox.db'
result = asyncio.run(execute_api_request(ExecuteApiRequestArgs(
    method='POST', path='/orders', body={'product_id': 4, 'quantity': 1},
)))
assert result.success and result.data is not None
assert result.data.status_code == 500 and result.data.request_id
logs = inspect_server_logs(InspectServerLogsArgs(request_id=result.data.request_id))
assert logs.success and logs.data is not None
assert any(e.event_type == 'request_failed' for e in logs.data.events)
print('Shared SQLite correlation verified:', result.data.request_id)

provider = configure_tracing()
assert provider is not None, 'Enable Docker tracing for this check'
try:
    with get_tracer(provider).start_as_current_span('docker-runtime-smoke') as span:
        trace_id = format(span.get_span_context().trace_id, '032x')
    assert provider.force_flush()
    print('Synthetic trace ID:', trace_id)
finally:
    shutdown_tracing(provider)
PY
```

Open Phoenix, select the `apilens` project (or your configured project name), and
confirm `docker-runtime-smoke` with the printed trace ID exists. A successful
flush alone does not prove ingestion. The synthetic span contains no bug report,
API key, request/response body, log message, or source. It verifies transport,
not live Agent quality. Do not use `/debug` as a free startup check.

## Operate and reset

```bash
docker compose ps
docker compose logs --tail=100
docker compose stop       # Stop containers, keep data.
docker compose start      # Restart existing containers.
docker compose down       # Remove containers/network, keep data.
docker compose up --build # Rebuild after changing source or dependency constraints.
```

Named volumes survive container recreation and `down`. Sandbox initialization
seeds missing fixture rows without clearing orders, inventory changes, or logs.
The Compose database is separate from the host's `sandbox.db`. To discard **both**
Sandbox state and Phoenix traces, stop the stack and remove its volumes:

```bash
docker compose down --volumes
docker compose up --build
```

Both upstream images are pinned by version and multi-platform manifest digest.
The shared Dockerfile installs runtime dependencies using `constraints-dev.txt`,
the same pinned resolution as CI, without development extras. Setuptools is
pinned and build isolation is disabled to avoid an unconstrained build dependency
resolution. The allowlist in `.dockerignore` excludes local secrets, databases,
Git history, tests, evaluation artifacts, and virtual environments. Application
containers run as an unprivileged user and retain source for the implementation
inspection tool. No host source tree or Docker socket is mounted. Host Python
installation and Uvicorn commands in the README remain available unchanged.
