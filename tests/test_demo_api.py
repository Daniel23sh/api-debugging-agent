"""Deterministic demo requests use real local tools and never contact OpenAI."""

import asyncio
import json
import socket
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError

from agent_service.agent.loop import DecisionProviderError, run_agent
from agent_service.agent.recording import SessionRecorder, current_recorder
from agent_service.agent.schemas import (
    DebugDiagnosis,
    FinalAnswerDecision,
    Observation,
    ToolCallDecision,
    ToolCallRecord,
)
from agent_service.agent.state import AgentLimits, DebugSessionState
from agent_service.demo import (
    DemoDebugResponse,
    DemoRecorder,
    DemoScenario,
    InvestigationStep,
    demo_sandbox,
)
from agent_service.main import DemoDebugRequest, create_app
from agent_service.observability import configure_tracing
from sandbox_api.db import Database
from sandbox_api.main import create_app as create_sandbox_app

PRIVATE = "PRIVATE-sk-secret-user-body-source-log-reasoning"
REQUEST_ID = "6c0df0f0-0a17-47f8-858e-8a92f3762c65"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Demo tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "")


@pytest.fixture
def temporary_paths(monkeypatch, tmp_path):
    paths = []

    def temporary(**kwargs):
        directory = TemporaryDirectory(dir=tmp_path, **kwargs)
        paths.append(Path(directory.name))
        return directory

    monkeypatch.setattr("agent_service.demo.TemporaryDirectory", temporary)
    return paths


def tool(name="execute_api_request", **arguments):
    return ToolCallDecision(kind="tool_call", tool_name=name, arguments=arguments)


def final():
    return FinalAnswerDecision(
        kind="final_answer",
        diagnosis=DebugDiagnosis(
            status="INCONCLUSIVE", missing_evidence=["More evidence is needed"]
        ),
    )


class Scripted:
    def __init__(self, *decisions):
        self.decisions = iter(decisions)
        self.states = []

    async def next_decision(self, state):
        self.states.append(state)
        return next(self.decisions)


def client_for(provider):
    return TestClient(create_app(lambda: provider, tracing_factory=lambda: None))


def test_demo_openapi_is_additive_and_typed():
    schema = create_app().openapi()
    schemas = schema["components"]["schemas"]
    operation = schema["paths"]["/demo/debug"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DemoDebugRequest"
    }
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DemoDebugResponse"
    }
    request = schemas["DemoDebugRequest"]
    assert request["required"] == ["issue"]
    assert set(request["properties"]) == {"issue", "scenario_id"}
    assert request["properties"]["issue"] == schemas["DebugRequest"]["properties"]["issue"]
    assert request["additionalProperties"] is False
    assert schemas["DemoScenario"]["enum"] == [item.value for item in DemoScenario]
    response = schemas["DemoDebugResponse"]
    assert response["properties"]["diagnosis"] == {"$ref": "#/components/schemas/DebugDiagnosis"}
    assert response["properties"]["steps"]["items"] == {"$ref": "#/components/schemas/InvestigationStep"}
    assert response["additionalProperties"] is False


@pytest.mark.parametrize("values", [
    {"kind": "invented"},
    {"kind": "tool_attempt", "tool_name": PRIVATE},
    {"kind": "tool_attempt", "method": PRIVATE},
    {"kind": "tool_attempt", "route": f"/products/{PRIVATE}"},
    {"kind": "tool_attempt", "status_code": 600},
    {"kind": "tool_attempt", "status_code": True},
    {"kind": "tool_attempt", "request_id": PRIVATE},
    {"kind": "tool_attempt", "error_category": PRIVATE},
    {"kind": "tool_attempt", "reasoning": PRIVATE},
])
def test_response_step_schema_rejects_unapproved_values(values):
    with pytest.raises(ValidationError):
        InvestigationStep.model_validate(values)


def test_response_requires_a_valid_existing_diagnosis():
    with pytest.raises(ValidationError):
        DemoDebugResponse.model_validate({"diagnosis": {"status": "RESOLVED"}, "steps": []})


@pytest.mark.parametrize("scenario", [None, *DemoScenario])
def test_early_final_is_real_and_has_empty_timeline(scenario, temporary_paths):
    provider = Scripted(final())
    response = client_for(provider).post(
        "/demo/debug", json={"issue": "  A vague report  ", "scenario_id": scenario}
    )
    assert response.status_code == 200
    result = DemoDebugResponse.model_validate(response.json())
    assert result.diagnosis == final().diagnosis
    assert result.steps == []
    assert provider.states[0].issue == "A vague report"
    assert provider.states[0].step_count == provider.states[0].tool_call_count == 0
    assert provider.states[0].observations == []
    assert len(temporary_paths) == 1 and not temporary_paths[0].exists()


@pytest.mark.parametrize("scenario,method,path,body,status", [
    ("missing-inventory", "POST", "/orders", {"product_id": 4, "quantity": 1}, 500),
    ("wrong-status", "GET", "/products/999", None, 200),
    ("incorrect-total", "POST", "/orders", {"product_id": 2, "quantity": 2}, 201),
    ("response-schema", "GET", "/orders/1", None, 200),
])
def test_scenarios_reproduce_actual_sandbox_bugs(
    scenario, method, path, body, status, temporary_paths,
):
    class ScenarioProvider(Scripted):
        async def next_decision(self, state):
            if not self.states:
                database = Database(temporary_paths[-1] / "sandbox.db")
                assert database.get_product(4) is not None
                assert database.get_inventory(4) is None
                with database.connect() as connection:
                    assert connection.execute("SELECT count(*) FROM orders").fetchone()[0] == (
                        1 if scenario == "response-schema" else 0
                    )
                    assert connection.execute("SELECT count(*) FROM request_logs").fetchone()[0] == 0
            return await super().next_decision(state)

    provider = ScenarioProvider(tool(method=method, path=path, body=body), final())
    response = client_for(provider).post(
        "/demo/debug", json={"issue": "Investigate this report", "scenario_id": scenario}
    )
    assert response.status_code == 200
    result = DemoDebugResponse.model_validate(response.json())
    assert len(result.steps) == 1
    assert result.steps[0].kind == "tool_attempt"
    assert result.steps[0].success is True  # HTTP 500 is still a successful probe.
    assert result.steps[0].status_code == status
    observation = provider.states[-1].observations[0]
    assert str(result.steps[0].request_id) == observation.request_id
    actual_body = observation.data["response_body"]
    if scenario == "missing-inventory":
        assert actual_body == {"detail": "Internal Server Error"}
    elif scenario == "wrong-status":
        assert actual_body == {}
    elif scenario == "incorrect-total":
        assert actual_body["quantity"] == 2 and actual_body["total"] == "129.50"
    else:
        assert actual_body["productId"] == 1 and "product_id" not in actual_body
        assert actual_body["id"] == 1
    assert not temporary_paths[-1].exists()


def test_real_tools_preserve_order_and_correlated_logs(temporary_paths):
    class Investigation:
        async def next_decision(self, state):
            if not state.observations:
                return tool(method="POST", path="/orders", body={"product_id": 4, "quantity": 1})
            if len(state.observations) == 1:
                return tool("inspect_server_logs", request_id=state.request_ids[0])
            if len(state.observations) == 2:
                events = state.observations[-1].data["events"]
                assert [event["event_type"] for event in events] == ["request_received", "request_failed"]
                assert events[-1]["status_code"] == 500
                return tool("inspect_api_spec", method="POST", path="/orders")
            if len(state.observations) == 3:
                return tool("inspect_endpoint_implementation", method="POST", path="/orders")
            return final()

    response = client_for(Investigation()).post("/demo/debug", json={"issue": PRIVATE})
    result = DemoDebugResponse.model_validate(response.json())
    assert [step.tool_name for step in result.steps] == [
        "execute_api_request", "inspect_server_logs", "inspect_api_spec", "inspect_endpoint_implementation",
    ]
    assert all(step.kind == "tool_attempt" and step.success for step in result.steps)
    assert result.steps[0].status_code == 500
    assert result.steps[1].request_id == result.steps[0].request_id
    assert [step.route for step in result.steps] == ["/orders", None, "/orders", "/orders"]
    assert PRIVATE not in response.text
    assert "TypeError" not in response.text and "source" not in response.text
    assert not temporary_paths[-1].exists()


def test_failures_and_rejected_actions_do_not_look_like_executions():
    provider = Scripted(
        tool(PRIVATE, secret=PRIVATE),
        tool("inspect_server_logs", request_id=PRIVATE),
        tool(method="GET", path=f"/forbidden/{PRIVATE}"),
        tool("inspect_server_logs", request_id=REQUEST_ID),
        final(),
    )
    response = client_for(provider).post("/demo/debug", json={"issue": PRIVATE})
    result = DemoDebugResponse.model_validate(response.json())
    assert [step.kind for step in result.steps] == ["rejected_action"] * 2 + ["tool_attempt"] * 2
    assert [step.error_category for step in result.steps] == [
        "TOOL_NOT_FOUND", "TOOL_ARGUMENT_VALIDATION_ERROR", "ENDPOINT_NOT_ALLOWED", "LOG_NOT_FOUND",
    ]
    assert not any(step.success for step in result.steps)
    assert result.steps[0].tool_name is None and result.steps[2].route is None
    assert result.steps[-1].request_id == UUID(REQUEST_ID)
    assert PRIVATE not in response.text


@pytest.mark.parametrize("payload", [
    {}, {"issue": ""}, {"issue": " \n\t "}, {"issue": "x" * 4001},
    {"issue": "report", "scenario_id": "unknown"},
    {"issue": "report", "scenario_id": 3},
    {"issue": "report", "sql": "DELETE FROM orders"},
    {"issue": "report", "setup_requests": []},
])
def test_invalid_input_never_constructs_provider_or_fixtures(payload, temporary_paths):
    def forbidden():
        pytest.fail("Invalid input must not construct a provider")

    response = TestClient(create_app(forbidden)).post("/demo/debug", json=payload)
    assert response.status_code == 422
    assert temporary_paths == []


def test_demo_preserves_issue_length_and_trimming():
    provider = Scripted(final())
    response = client_for(provider).post("/demo/debug", json={"issue": f" \n{'é' * 4000}\t "})
    assert response.status_code == 200
    assert provider.states[0].issue == "é" * 4000
    assert DemoDebugRequest(issue="report").scenario_id is None


@pytest.mark.parametrize("key", ["", " \t "])
def test_missing_key_is_safe_and_does_not_start_session(monkeypatch, temporary_paths, key):
    monkeypatch.setenv("OPENAI_API_KEY", key)

    def forbidden(*args, **kwargs):
        pytest.fail("Missing credentials must not construct an OpenAI client")

    monkeypatch.setattr("agent_service.agent.openai_provider.AsyncOpenAI", forbidden)
    response = TestClient(create_app()).post("/demo/debug", json={"issue": PRIVATE})
    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]
    assert PRIVATE not in response.text and temporary_paths == []


@pytest.mark.parametrize("failure", ["constructor", "decision", "typed_provider", "tool", "setup"])
def test_failure_responses_are_safe_and_dispose_resources(monkeypatch, temporary_paths, failure):
    class Failing:
        async def next_decision(self, state):
            if failure == "typed_provider":
                raise DecisionProviderError(PRIVATE, PRIVATE)
            if failure == "decision":
                raise RuntimeError(PRIVATE)
            return tool(method="GET", path="/products/1")

    def factory():
        if failure == "constructor":
            raise RuntimeError(PRIVATE)
        return Failing()

    if failure == "tool":
        async def broken(*args, **kwargs):
            raise RuntimeError(PRIVATE)
        monkeypatch.setattr("agent_service.agent.loop.dispatch_tool_call", broken)
    elif failure == "setup":
        def broken(*args, **kwargs):
            raise RuntimeError(PRIVATE)
        monkeypatch.setattr("agent_service.demo.Database.initialize", broken)
    response = TestClient(create_app(factory)).post("/demo/debug", json={"issue": PRIVATE})
    assert response.status_code == 502
    assert PRIVATE not in response.text
    assert set(response.json()) == {"detail"}
    assert len(temporary_paths) == 1 and not temporary_paths[0].exists()
    assert current_recorder.get() is None


@pytest.mark.asyncio
async def test_timeout_retry_records_actual_attempts(monkeypatch):
    from agent_service.tools.execute_api_request import execute_api_request as execute

    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout(PRIVATE, request=request)
        return httpx.Response(200, json={"private": PRIVATE}, headers={"X-Request-ID": REQUEST_ID})

    async def timed_request(args, **kwargs):
        return await execute(args, transport=httpx.MockTransport(respond))

    monkeypatch.setattr("agent_service.agent.dispatch.execute_api_request", timed_request)
    provider = Scripted(tool(method="GET", path="/products/1"), final())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(lambda: provider)), base_url="http://agent") as client:
        response = await client.post("/demo/debug", json={"issue": PRIVATE})
    steps = DemoDebugResponse.model_validate(response.json()).steps
    assert calls == 2
    assert [step.retry for step in steps] == [False, True]
    assert [step.success for step in steps] == [False, True]
    assert steps[0].error_category == "TOOL_TIMEOUT"
    assert steps[1].status_code == 200 and steps[1].request_id == UUID(REQUEST_ID)
    assert PRIVATE not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["decisions", "identical", "tools", "tool_timeout", "decision_timeout"])
async def test_limit_outcomes_are_recorded_and_raw_fallback_evidence_is_not_exported(
    monkeypatch, temporary_paths, limit,
):
    original_run = run_agent
    limits = AgentLimits(max_agent_decisions=2) if limit == "decisions" else (
        AgentLimits(max_tool_calls=1) if limit == "tools" else AgentLimits()
    )
    if limit.endswith("timeout"):
        limits = AgentLimits(session_timeout_seconds=1)

    async def limited_run(*args, **kwargs):
        return await original_run(*args, limits=limits, **kwargs)

    monkeypatch.setattr("agent_service.main.run_agent", limited_run)
    if limit == "decisions":
        provider = Scripted(
            tool("inspect_endpoint_implementation", method="GET", path="/products/1"),
            tool(PRIVATE),
        )
    elif limit in {"tools", "identical"}:
        provider = Scripted(*[
            tool(method="GET", path="/products/1") for _ in range(8)
        ])
    else:
        class Hanging:
            async def next_decision(self, state):
                if limit == "decision_timeout":
                    await asyncio.sleep(10)
                return tool(method="GET", path="/products/1")
        provider = Hanging()
        if limit == "tool_timeout":
            async def slow(*args, **kwargs):
                await asyncio.sleep(10)
            monkeypatch.setattr("agent_service.agent.loop.dispatch_tool_call", slow)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(lambda: provider)), base_url="http://agent") as client:
        response = await client.post("/demo/debug", json={"issue": PRIVATE})
    assert response.status_code == 200
    result = DemoDebugResponse.model_validate(response.json())
    assert result.diagnosis.status == "LIMIT_REACHED"
    assert result.diagnosis.evidence == [] and result.diagnosis.missing_evidence
    if limit == "decisions":
        assert [step.kind for step in result.steps] == ["tool_attempt", "rejected_action"]
    elif limit in {"tools", "identical"}:
        executed = 1 if limit == "tools" else 2
        assert sum(step.kind == "tool_attempt" for step in result.steps) == executed
        assert all(step.kind == "rejected_action" for step in result.steps[executed:])
        expected = "TOOL_CALL_LIMIT_REACHED" if limit == "tools" else "IDENTICAL_TOOL_CALL_LIMIT_REACHED"
        assert all(step.error_category == expected for step in result.steps[executed:])
    else:
        assert len(result.steps) == 1
        assert result.steps[0].error_category == "SESSION_TIMEOUT"
        assert result.steps[0].kind == ("tool_attempt" if limit == "tool_timeout" else "session_error")
    assert PRIVATE not in response.text and '"source"' not in response.text
    assert not temporary_paths[-1].exists()


@pytest.mark.asyncio
async def test_repeated_and_concurrent_sessions_cannot_touch_persistent_sandbox(tmp_path, temporary_paths):
    persistent = Database(tmp_path / "persistent.db")
    persistent.initialize()
    persistent.create_order(1, 1)
    persistent.add_request_log(request_id=REQUEST_ID, event_type=PRIVATE, method="GET", path="/products/1")
    before = Path(persistent.path).read_bytes()
    providers = []
    barrier = asyncio.Barrier(2)
    concurrent = False

    class Mutating(Scripted):
        async def next_decision(self, state):
            count = len(state.observations)
            if count == 0:
                if concurrent:
                    await barrier.wait()
                self.states.append(state)
                return tool(method="POST", path="/orders", body={"product_id": 1, "quantity": 2})
            if count == 1:
                self.states.append(state)
                return tool(method="PATCH", path="/inventory/1", body={"quantity": 20})
            if count == 2:
                self.states.append(state)
                return tool("inspect_server_logs", request_id=state.request_ids[0])
            self.states.append(state)
            return final()

    def factory():
        provider = Mutating()
        providers.append(provider)
        return provider

    app = create_app(factory, database=persistent, transport=httpx.ASGITransport(app=create_sandbox_app(persistent.path)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
        for _ in range(2):
            assert (await client.post("/demo/debug", json={"issue": "report"})).status_code == 200
        concurrent = True
        responses = await asyncio.gather(*[
            client.post("/demo/debug", json={"issue": "report"}) for _ in range(2)
        ])
        assert all(response.status_code == 200 for response in responses)
        assert Path(persistent.path).read_bytes() == before
        assert len({provider.states[0].session_id for provider in providers}) == 4
        ids = set()
        for provider in providers:
            observations = provider.states[-1].observations
            assert observations[0].data["response_body"]["id"] == 1
            assert observations[0].data["status_code"] == 201
            assert observations[1].data["response_body"]["quantity"] == 20
            assert observations[2].request_id == observations[0].request_id
            ids.add(observations[0].request_id)
        assert len(ids) == 4
        # /debug still uses the injected persistent Sandbox and its current state.
        concurrent = False
        response = await client.post("/debug", json={"issue": "report"})
        assert response.status_code == 200 and "steps" not in response.json()
        assert providers[-1].states[-1].observations[0].data["response_body"]["id"] == 2
        assert Path(persistent.path).read_bytes() != before
    assert len(temporary_paths) == 4
    assert all(not path.exists() for path in temporary_paths)


@pytest.mark.asyncio
async def test_cancellation_cleans_up_and_resets_recorder(temporary_paths):
    entered = asyncio.Event()

    class Waiting:
        async def next_decision(self, state):
            entered.set()
            await asyncio.sleep(10)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(Waiting)), base_url="http://agent") as client:
        task = asyncio.create_task(client.post("/demo/debug", json={"issue": "report"}))
        await entered.wait()
        assert temporary_paths[-1].exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not temporary_paths[-1].exists()
    assert current_recorder.get() is None


@pytest.mark.asyncio
async def test_cancelled_threaded_log_reader_cannot_recreate_disposed_state(
    monkeypatch, temporary_paths,
):
    from agent_service.tools.inspect_server_logs import inspect_server_logs

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    results = []

    def slow_reader(args, *, database=None):
        entered.set()
        try:
            assert release.wait(5)
            results.append(inspect_server_logs(args, database=database))
        finally:
            finished.set()

    monkeypatch.setattr("agent_service.agent.dispatch.inspect_server_logs", slow_reader)
    provider = Scripted(tool("inspect_server_logs", request_id=REQUEST_ID))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(lambda: provider)), base_url="http://agent") as client:
        task = asyncio.create_task(client.post("/demo/debug", json={"issue": "report"}))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not temporary_paths[-1].exists()
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 2)
    assert results[0].error_type == "TOOL_DATABASE_ERROR"
    assert not temporary_paths[-1].exists()


@pytest.mark.parametrize("path", [f"/products/{PRIVATE}", f"/products/{PRIVATE}?key={PRIVATE}", f"/products/%2e%2e/{PRIVATE}"])
def test_recorder_exports_only_allowlisted_metadata(path):
    recorder: SessionRecorder = DemoRecorder()
    state = DebugSessionState(session_id=PRIVATE, issue=PRIVATE)
    recorder.start(state, AgentLimits())
    recorder.decision_started(state)
    call = ToolCallRecord(tool_name="execute_api_request", arguments={
        "method": "GET", "path": path, "body": {"secret": PRIVATE}, "request_id": PRIVATE,
    })
    recorder.tool_started(call, retry=False)
    recorder.model_started(PRIVATE)
    recorder.model_finished(PRIVATE, 123, {PRIVATE: 123}, PRIVATE)
    recorder.tool_finished(Observation(tool_name=PRIVATE, success=True, request_id=PRIVATE, data={
        "status_code": 200, "response_body": PRIVATE, "sections": PRIVATE,
        "events": [{"error_message": PRIVATE}], "reasoning": PRIVATE,
    }))
    recorder.decision_started(state)
    recorder.decision_returned(tool(PRIVATE, method=PRIVATE, path=PRIVATE))
    recorder.error("decision", PRIVATE)
    recorder.finish(state, final().diagnosis)
    encoded = json.dumps([step.model_dump(mode="json") for step in recorder.steps])
    assert PRIVATE not in encoded and "123" not in encoded
    assert recorder.steps[0].request_id is None
    assert recorder.steps[0].route == ("/products/{product_id}" if path.endswith(PRIVATE) and "?" not in path and "%" not in path else None)
    assert recorder.steps[1].tool_name is None
    assert recorder.steps[1].error_category == "EXECUTION_ERROR"
    assert recorder._selected is None


@pytest.mark.asyncio
async def test_uuid_and_status_metadata_are_canonical_and_strict():
    recorder = DemoRecorder()
    recorder.tool_started(ToolCallRecord(tool_name="inspect_server_logs", arguments={"request_id": REQUEST_ID.upper()}), False)
    recorder.tool_finished(Observation(tool_name="inspect_server_logs", success=True, data={"status_code": True}))
    assert str(recorder.steps[0].request_id) == REQUEST_ID
    assert recorder.steps[0].status_code is None
    async with demo_sandbox(DemoScenario.RESPONSE_SCHEMA) as (database, _):
        assert database.get_order(1).product_id == 1
    assert not Path(database.path).parent.exists()


def test_demo_uses_existing_trace_hierarchy_without_private_fields():
    exporter = InMemorySpanExporter()
    tracer_provider = configure_tracing(span_exporter=exporter)
    app = create_app(
        lambda: Scripted(tool(method="GET", path="/products/999"), final()),
        tracing_factory=lambda: tracer_provider,
    )
    with TestClient(app) as client:
        response = client.post("/demo/debug", json={"issue": PRIVATE})
    assert response.status_code == 200
    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["agent_decision", "execute_api_request", "agent_decision", "final_diagnosis", "DebugSession"]
    session = spans[-1]
    assert all(span.parent.span_id == session.context.span_id for span in spans[:-1])
    assert PRIVATE not in str([span.attributes for span in spans])
