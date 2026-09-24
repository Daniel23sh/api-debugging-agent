import asyncio
import json
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI
from openinference.instrumentation import REDACTED_VALUE
from openinference.instrumentation.openai import OpenAIInstrumentor
from openinference.semconv.trace import (
    OpenInferenceSpanKindValues,
    SpanAttributes,
    ToolCallAttributes,
)
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.semconv.attributes.error_attributes import ERROR_TYPE
from opentelemetry.trace import StatusCode
from phoenix.otel import PROJECT_NAME

from agent_service.agent import (
    DebugDiagnosis,
    DebugSessionState,
    FinalAnswerDecision,
    OpenAIDecisionProvider,
    ToolCallDecision,
    dispatch_tool_call,
    run_agent,
)
from agent_service.main import create_app
from agent_service.observability import (
    DEFAULT_PROJECT_NAME,
    DEFAULT_SERVICE_NAME,
    configure_openai_instrumentation,
    configure_tracing,
    get_tracer,
    shutdown_tracing,
)
from sandbox_api.db import Database
from sandbox_api.main import REQUEST_ID_HEADER
from sandbox_api.main import create_app as create_sandbox_app


class RecordingExporter(InMemorySpanExporter):
    def __init__(self) -> None:
        super().__init__()
        self.shutdown_count = 0

    def shutdown(self) -> None:
        self.shutdown_count += 1
        super().shutdown()


def sdk_final_response(
    response_id: str,
    *,
    diagnosis: dict | None = None,
    missing_evidence: str = "More evidence is needed",
) -> dict:
    diagnosis = diagnosis or {
        "status": "INCONCLUSIVE",
        "affected_endpoint": None,
        "root_cause": None,
        "evidence": [],
        "missing_evidence": [missing_evidence],
        "suggested_fix": None,
    }
    return {
        "id": response_id,
        "object": "response",
        "created_at": 0,
        "model": "gpt-6-sol",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "id": f"message-{response_id}",
                "status": "completed",
                "role": "assistant",
                "phase": "final_answer",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(diagnosis),
                        "annotations": [],
                    }
                ],
            }
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 1},
        },
    }


def sdk_tool_response(response_id: str, *, arguments: str) -> dict:
    return {
        "id": response_id,
        "object": "response",
        "created_at": 0,
        "model": "gpt-6-sol",
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "id": f"function-{response_id}",
                "status": "completed",
                "name": "execute_api_request",
                "call_id": f"call-{response_id}",
                "arguments": arguments,
            }
        ],
        "usage": {
            "input_tokens": 8,
            "output_tokens": 4,
            "total_tokens": 12,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 1},
        },
    }


def test_tracing_is_disabled_without_a_collector(monkeypatch) -> None:
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)

    assert configure_tracing() is None
    assert configure_openai_instrumentation(None) is None
    shutdown_tracing(None)


def test_tracing_supports_an_in_memory_exporter(monkeypatch) -> None:
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)
    exporter = RecordingExporter()
    provider = configure_tracing(span_exporter=exporter)

    assert provider is not None
    assert provider.resource.attributes[PROJECT_NAME] == DEFAULT_PROJECT_NAME
    assert provider.resource.attributes["service.name"] == DEFAULT_SERVICE_NAME

    with get_tracer(provider).start_as_current_span("foundation-test"):
        pass

    shutdown_tracing(provider)

    assert [span.name for span in exporter.get_finished_spans()] == [
        "foundation-test"
    ]
    assert exporter.shutdown_count == 1


def test_repeated_app_lifespans_own_independent_non_global_providers() -> None:
    global_provider = trace.get_tracer_provider()
    exporters: list[RecordingExporter] = []
    providers = []

    def tracing_factory():
        exporter = RecordingExporter()
        provider = configure_tracing(span_exporter=exporter)
        exporters.append(exporter)
        providers.append(provider)
        return provider

    async def make_model_request(response_id: str) -> None:
        def respond(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=sdk_final_response(response_id))

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond)
        ) as http_client:
            client = AsyncOpenAI(
                api_key="test-key", http_client=http_client, max_retries=0
            )
            decision = await OpenAIDecisionProvider(client=client).next_decision(
                DebugSessionState(session_id=response_id, issue="safe test")
            )
            assert decision.diagnosis.status == "INCONCLUSIVE"

    instrumentor = OpenAIInstrumentor()
    assert not instrumentor.is_instrumented_by_opentelemetry
    for index in range(2):
        app = create_app(tracing_factory=tracing_factory)
        with TestClient(app):
            assert app.state.tracer_provider is providers[-1]
            assert app.state.openai_instrumentor is instrumentor
            assert instrumentor.is_instrumented_by_opentelemetry
            asyncio.run(make_model_request(f"response-{index}"))
        assert not instrumentor.is_instrumented_by_opentelemetry

    assert providers[0] is not providers[1]
    assert trace.get_tracer_provider() is global_provider
    assert [exporter.shutdown_count for exporter in exporters] == [1, 1]
    assert [
        sum(
            span.attributes.get(SpanAttributes.OPENINFERENCE_SPAN_KIND)
            == OpenInferenceSpanKindValues.LLM.value
            for span in exporter.get_finished_spans()
        )
        for exporter in exporters
    ] == [1, 1]


class TraceProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.decision_span_ids: list[int] = []

    async def next_decision(self, state: DebugSessionState):
        self.decision_span_ids.append(
            trace.get_current_span().get_span_context().span_id
        )
        self.calls += 1
        if self.calls == 1:
            return ToolCallDecision(
                kind="tool_call", tool_name="unknown", arguments={}
            )
        if self.calls == 2:
            return ToolCallDecision(
                kind="tool_call",
                tool_name="execute_api_request",
                arguments={
                    "method": "POST",
                    "path": "/orders",
                    "body": {"api_key": "do-not-trace-this-secret"},
                },
            )
        return FinalAnswerDecision(
            kind="final_answer",
            diagnosis=DebugDiagnosis(
                status="RESOLVED",
                affected_endpoint="POST /orders",
                root_cause="do-not-trace-this-root-cause",
                evidence=[
                    {"source": "api_execution", "finding": "Observed response"}
                ],
                suggested_fix="do-not-trace-this-fix",
            ),
        )


def test_debug_session_emits_safe_orchestration_hierarchy(monkeypatch) -> None:
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)
    exporter = RecordingExporter()
    tracer_provider = configure_tracing(span_exporter=exporter)
    assert tracer_provider is not None
    provider = TraceProvider()
    execution_parent_ids: list[int] = []
    request_id = "6c0df0f0-0a17-47f8-858e-8a92f3762c65"

    def respond(request: httpx.Request) -> httpx.Response:
        execution_parent_ids.append(
            trace.get_current_span().get_span_context().span_id
        )
        return httpx.Response(
            500,
            json={"detail": "do-not-trace-this-response"},
            headers={REQUEST_ID_HEADER: request_id},
        )

    app = create_app(
        lambda: provider,
        transport=httpx.MockTransport(respond),
        tracing_factory=lambda: tracer_provider,
    )
    with TestClient(app) as client:
        response = client.post(
            "/debug", json={"issue": "API key do-not-trace-this-input"}
        )
    diagnosis = DebugDiagnosis.model_validate(response.json())

    spans = exporter.get_finished_spans()
    session = next(span for span in spans if span.name == "DebugSession")
    decisions = [span for span in spans if span.name == "agent_decision"]
    tools = [
        span
        for span in spans
        if span.attributes.get(SpanAttributes.OPENINFERENCE_SPAN_KIND)
        == OpenInferenceSpanKindValues.TOOL.value
    ]
    final = next(span for span in spans if span.name == "final_diagnosis")

    assert diagnosis.status == "RESOLVED"
    assert exporter.shutdown_count == 1
    assert len(spans) == 6
    assert len(decisions) == provider.calls == 3
    assert len(tools) == 1
    assert all(span.context.trace_id == session.context.trace_id for span in spans)
    assert session.parent is None
    assert all(span.parent.span_id == session.context.span_id for span in decisions)
    assert final.parent.span_id == session.context.span_id
    tool = tools[0]
    assert tool.name == "execute_api_request"
    assert tool.parent.span_id == session.context.span_id
    assert all(
        span.attributes[SpanAttributes.OPENINFERENCE_SPAN_KIND]
        == OpenInferenceSpanKindValues.AGENT.value
        for span in decisions
    )
    assert provider.decision_span_ids == [span.context.span_id for span in decisions]
    assert execution_parent_ids == [tool.context.span_id]

    first, tool_call, final_answer = decisions
    assert first.attributes[ERROR_TYPE] == "TOOL_NOT_FOUND"
    assert first.status.status_code is StatusCode.ERROR
    assert tool_call.attributes["apilens.decision.selected_action"] == "TOOL_CALL"
    assert (
        tool_call.attributes[ToolCallAttributes.TOOL_CALL_FUNCTION_NAME]
        == "execute_api_request"
    )
    assert json.loads(
        tool_call.attributes[
            ToolCallAttributes.TOOL_CALL_FUNCTION_ARGUMENTS_JSON
        ]
    ) == {"method": "POST", "path": "/orders"}
    assert tool.attributes[SpanAttributes.TOOL_NAME] == "execute_api_request"
    assert json.loads(
        tool.attributes[ToolCallAttributes.TOOL_CALL_FUNCTION_ARGUMENTS_JSON]
    ) == {"method": "POST", "path": "/orders"}
    assert tool.attributes["apilens.tool.success"] is True
    assert tool.attributes["apilens.tool.method"] == "POST"
    assert tool.attributes["apilens.tool.path"] == "/orders"
    assert tool.attributes["apilens.tool.status_code"] == 500
    assert tool.attributes["apilens.tool.request_id"] == request_id
    assert tool.status.status_code is StatusCode.UNSET
    assert final_answer.attributes["apilens.decision.selected_action"] == (
        "FINAL_ANSWER"
    )

    assert UUID(session.attributes[SpanAttributes.SESSION_ID])
    assert session.attributes["apilens.session.final_status"] == "RESOLVED"
    assert session.attributes["apilens.session.decision_count"] == 3
    assert session.attributes["apilens.session.tool_call_count"] == 1
    assert session.attributes[SpanAttributes.OPENINFERENCE_SPAN_KIND] == (
        OpenInferenceSpanKindValues.AGENT.value
    )
    assert final.attributes["apilens.diagnosis.status"] == "RESOLVED"
    assert final.attributes["apilens.diagnosis.affected_endpoint"] == (
        "POST /orders"
    )
    assert final.attributes["apilens.diagnosis.evidence_count"] == 1
    assert final.attributes["apilens.diagnosis.root_cause_present"] is True
    assert final.attributes["apilens.diagnosis.suggested_fix_present"] is True
    assert final.attributes[SpanAttributes.OPENINFERENCE_SPAN_KIND] == (
        OpenInferenceSpanKindValues.CHAIN.value
    )

    trace_data = repr([span.attributes for span in spans])
    assert "do-not-trace-this" not in trace_data
    assert "unknown" not in {span.name for span in spans}


def test_real_openai_instrumentation_preserves_hierarchy_and_privacy(
    monkeypatch,
) -> None:
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)
    exporter = RecordingExporter()
    tracer_provider = configure_tracing(span_exporter=exporter)
    assert tracer_provider is not None
    model_requests: list[dict] = []
    response_marker = "do-not-trace-this-tool-observation"
    diagnosis = {
        "status": "RESOLVED",
        "affected_endpoint": "POST /orders",
        "root_cause": "do-not-trace-this-root-cause",
        "evidence": [
            {"source": "api_execution", "finding": "do-not-trace-this-evidence"}
        ],
        "missing_evidence": [],
        "suggested_fix": "do-not-trace-this-suggested-fix",
    }

    def model_response(request: httpx.Request) -> httpx.Response:
        model_requests.append(json.loads(request.content))
        if len(model_requests) == 1:
            arguments = json.dumps(
                {
                    "method": "POST",
                    "path": "/orders",
                    "body": json.dumps(
                        {"api_key": "do-not-trace-this-model-tool-argument"}
                    ),
                }
            )
            return httpx.Response(
                200,
                json=sdk_tool_response("model-1", arguments=arguments),
            )
        assert response_marker in model_requests[1]["input"][0]["output"]
        return httpx.Response(
            200,
            json=sdk_final_response("model-2", diagnosis=diagnosis),
        )

    def tool_response(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"detail": response_marker},
            headers={REQUEST_ID_HEADER: "request-5d"},
        )

    openai_http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(model_response)
    )
    openai_client = AsyncOpenAI(
        api_key="sk-do-not-trace-this-api-key",
        http_client=openai_http_client,
        max_retries=0,
    )
    app = create_app(
        lambda: OpenAIDecisionProvider(client=openai_client),
        transport=httpx.MockTransport(tool_response),
        tracing_factory=lambda: tracer_provider,
    )
    with TestClient(app) as client:
        response = client.post(
            "/debug", json={"issue": "do-not-trace-this-user-issue"}
        )
    asyncio.run(openai_http_client.aclose())

    assert response.status_code == 200
    assert response.json()["status"] == "RESOLVED"
    spans = exporter.get_finished_spans()
    session = next(span for span in spans if span.name == "DebugSession")
    decisions = [span for span in spans if span.name == "agent_decision"]
    llm_spans = [
        span
        for span in spans
        if span.attributes.get(SpanAttributes.OPENINFERENCE_SPAN_KIND)
        == OpenInferenceSpanKindValues.LLM.value
    ]
    tool = next(span for span in spans if span.name == "execute_api_request")

    assert len(model_requests) == len(decisions) == len(llm_spans) == 2
    assert [span.parent.span_id for span in llm_spans] == [
        span.context.span_id for span in decisions
    ]
    assert all(span.context.trace_id == session.context.trace_id for span in spans)
    assert tool.parent.span_id == session.context.span_id
    assert all(span.parent.span_id != session.context.span_id for span in llm_spans)
    assert "model_call" not in {span.name for span in spans}
    assert all(
        span.attributes[SpanAttributes.LLM_MODEL_NAME] == "gpt-6-sol"
        for span in llm_spans
    )
    assert all(
        span.attributes[SpanAttributes.LLM_TOKEN_COUNT_TOTAL] > 0
        for span in llm_spans
    )
    assert all(
        span.attributes[SpanAttributes.INPUT_VALUE] == REDACTED_VALUE
        for span in llm_spans
    )
    assert all(
        span.attributes[SpanAttributes.OUTPUT_VALUE] == REDACTED_VALUE
        for span in llm_spans
    )
    assert not any(
        key.startswith(("llm.input_messages", "llm.output_messages", "llm.tools"))
        for span in llm_spans
        for key in span.attributes
    )
    trace_data = repr([span.attributes for span in spans])
    for secret in (
        "do-not-trace-this-user-issue",
        "do-not-trace-this-model-tool-argument",
        response_marker,
        "do-not-trace-this-root-cause",
        "do-not-trace-this-evidence",
        "do-not-trace-this-suggested-fix",
        "sk-do-not-trace-this-api-key",
    ):
        assert secret not in trace_data


def test_corrective_retry_creates_two_llm_spans_under_one_decision(
    monkeypatch,
) -> None:
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)
    exporter = RecordingExporter()
    tracer_provider = configure_tracing(span_exporter=exporter)
    assert tracer_provider is not None
    requests: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                200,
                json=sdk_tool_response("invalid", arguments="{"),
            )
        return httpx.Response(200, json=sdk_final_response("corrected"))

    openai_http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    openai_client = AsyncOpenAI(
        api_key="test-key", http_client=openai_http_client, max_retries=0
    )
    app = create_app(
        lambda: OpenAIDecisionProvider(client=openai_client),
        tracing_factory=lambda: tracer_provider,
    )
    with TestClient(app) as client:
        response = client.post("/debug", json={"issue": "format retry"})
    asyncio.run(openai_http_client.aclose())

    spans = exporter.get_finished_spans()
    decisions = [span for span in spans if span.name == "agent_decision"]
    llm_spans = [
        span
        for span in spans
        if span.attributes.get(SpanAttributes.OPENINFERENCE_SPAN_KIND)
        == OpenInferenceSpanKindValues.LLM.value
    ]
    tool_spans = [
        span
        for span in spans
        if span.attributes.get(SpanAttributes.OPENINFERENCE_SPAN_KIND)
        == OpenInferenceSpanKindValues.TOOL.value
    ]
    assert response.json()["status"] == "INCONCLUSIVE"
    assert len(requests) == len(llm_spans) == 2
    assert len(decisions) == 1
    assert tool_spans == []
    assert all(
        span.parent.span_id == decisions[0].context.span_id for span in llm_spans
    )
    assert "could not be accepted" in requests[1]["input"][1]["content"]


def test_tracing_disabled_leaves_real_openai_sdk_uninstrumented(monkeypatch) -> None:
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)

    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=sdk_final_response("disabled"))

    openai_http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    openai_client = AsyncOpenAI(
        api_key="test-key", http_client=openai_http_client, max_retries=0
    )
    instrumentor = OpenAIInstrumentor()
    app = create_app(lambda: OpenAIDecisionProvider(client=openai_client))
    with TestClient(app) as client:
        assert app.state.tracer_provider is None
        assert app.state.openai_instrumentor is None
        assert not instrumentor.is_instrumented_by_opentelemetry
        response = client.post("/debug", json={"issue": "tracing disabled"})
    asyncio.run(openai_http_client.aclose())

    assert response.status_code == 200
    assert response.json()["status"] == "INCONCLUSIVE"
    assert not instrumentor.is_instrumented_by_opentelemetry


def test_openai_api_failure_marks_llm_and_decision_spans_as_errors(
    monkeypatch,
) -> None:
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)
    exporter = RecordingExporter()
    tracer_provider = configure_tracing(span_exporter=exporter)
    assert tracer_provider is not None
    requests = 0

    def fail(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            500,
            json={"error": {"message": "Temporary failure", "type": "server_error"}},
        )

    openai_http_client = httpx.AsyncClient(transport=httpx.MockTransport(fail))
    openai_client = AsyncOpenAI(
        api_key="test-key", http_client=openai_http_client, max_retries=0
    )
    app = create_app(
        lambda: OpenAIDecisionProvider(client=openai_client),
        tracing_factory=lambda: tracer_provider,
    )
    with TestClient(app) as client:
        response = client.post("/debug", json={"issue": "model unavailable"})
    asyncio.run(openai_http_client.aclose())

    spans = exporter.get_finished_spans()
    llm = next(
        span
        for span in spans
        if span.attributes.get(SpanAttributes.OPENINFERENCE_SPAN_KIND)
        == OpenInferenceSpanKindValues.LLM.value
    )
    decision = next(span for span in spans if span.name == "agent_decision")
    assert requests == 1
    assert response.json()["status"] == "INCONCLUSIVE"
    assert llm.status.status_code is StatusCode.ERROR
    assert decision.status.status_code is StatusCode.ERROR
    assert decision.attributes[ERROR_TYPE] == "MODEL_API_ERROR"


@pytest.mark.asyncio
async def test_typed_failure_is_an_error_but_rejections_emit_no_tool_span(
    monkeypatch,
) -> None:
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)
    exporter = RecordingExporter()
    tracer_provider = configure_tracing(span_exporter=exporter)
    assert tracer_provider is not None
    tracer = get_tracer(tracer_provider)

    failure = await dispatch_tool_call(
        ToolCallDecision(
            kind="tool_call",
            tool_name="inspect_api_spec",
            arguments={"method": "POST", "path": "/products/1"},
        ),
        tracer=tracer,
    )
    unknown = await dispatch_tool_call(
        ToolCallDecision(kind="tool_call", tool_name="unknown", arguments={}),
        tracer=tracer,
    )
    invalid = await dispatch_tool_call(
        ToolCallDecision(
            kind="tool_call",
            tool_name="execute_api_request",
            arguments={"method": "INVALID", "path": "/products/1"},
        ),
        tracer=tracer,
    )
    shutdown_tracing(tracer_provider)

    spans = exporter.get_finished_spans()
    assert failure.accepted and not failure.observation.success
    assert not unknown.accepted and not invalid.accepted
    assert [span.name for span in spans] == ["inspect_api_spec"]
    assert spans[0].status.status_code is StatusCode.ERROR
    assert spans[0].attributes[ERROR_TYPE] == "ENDPOINT_NOT_ALLOWED"
    assert spans[0].attributes["apilens.tool.success"] is False


@pytest.mark.asyncio
async def test_all_tool_spans_record_only_bounded_structural_metadata(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)
    exporter = RecordingExporter()
    tracer_provider = configure_tracing(span_exporter=exporter)
    assert tracer_provider is not None
    tracer = get_tracer(tracer_provider)
    database_path = tmp_path / "sandbox.db"
    database = Database(database_path)
    database.initialize()
    logged_request_id = uuid4()
    database.add_request_log(
        request_id=str(logged_request_id),
        event_type="request_failed",
        method="GET",
        path="/products/1",
        error_message="do-not-trace-this-log-message",
    )
    transport = httpx.ASGITransport(app=create_sandbox_app(database_path))
    calls = [
        ToolCallDecision(
            kind="tool_call",
            tool_name="inspect_api_spec",
            arguments={"method": "GET", "path": "/products/1"},
        ),
        ToolCallDecision(
            kind="tool_call",
            tool_name="execute_api_request",
            arguments={"method": "GET", "path": "/products/1"},
        ),
        ToolCallDecision(
            kind="tool_call",
            tool_name="inspect_server_logs",
            arguments={"request_id": str(logged_request_id)},
        ),
        ToolCallDecision(
            kind="tool_call",
            tool_name="inspect_endpoint_implementation",
            arguments={"method": "GET", "path": "/products/1"},
        ),
    ]
    results = [
        await dispatch_tool_call(
            call,
            transport=transport,
            database=database,
            tracer=tracer,
        )
        for call in calls
    ]
    shutdown_tracing(tracer_provider)

    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert all(result.accepted and result.observation.success for result in results)
    assert spans["inspect_api_spec"].attributes["apilens.tool.contract_found"] is True
    assert spans["inspect_api_spec"].attributes["apilens.tool.path"] == (
        "/products/{product_id}"
    )
    assert spans["execute_api_request"].attributes["apilens.tool.status_code"] == 200
    assert spans["execute_api_request"].attributes["apilens.tool.request_id"]
    assert spans["inspect_server_logs"].attributes["apilens.tool.logs_found"] is True
    assert spans["inspect_server_logs"].attributes["apilens.tool.event_count"] == 1
    assert spans["inspect_endpoint_implementation"].attributes[
        "apilens.tool.implementation_found"
    ] is True
    assert spans["inspect_endpoint_implementation"].attributes[
        "apilens.tool.section_count"
    ] > 0

    implementation = results[-1].observation.data["sections"][0]["source"]
    schema = results[0].observation.data["schemas"]
    trace_data = repr([span.attributes for span in spans.values()])
    assert implementation[:80] not in trace_data
    assert repr(schema) not in trace_data
    assert "do-not-trace-this-log-message" not in trace_data
    assert "Mechanical Keyboard" not in trace_data


class RetryProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def next_decision(self, state: DebugSessionState):
        self.calls += 1
        if self.calls == 1:
            return ToolCallDecision(
                kind="tool_call",
                tool_name="execute_api_request",
                arguments={"method": "GET", "path": "/products/1"},
            )
        return FinalAnswerDecision(
            kind="final_answer",
            diagnosis=DebugDiagnosis(
                status="INCONCLUSIVE", missing_evidence=["More evidence needed"]
            ),
        )


@pytest.mark.asyncio
async def test_timeout_retry_emits_one_tool_span_per_attempt(monkeypatch) -> None:
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)
    exporter = RecordingExporter()
    tracer_provider = configure_tracing(span_exporter=exporter)
    assert tracer_provider is not None
    tracer = get_tracer(tracer_provider)
    attempts = 0

    def recover(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("do-not-trace-this-timeout", request=request)
        return httpx.Response(200, json={"id": 1})

    state = DebugSessionState(session_id="retry-session", issue="retry safely")
    with tracer.start_as_current_span("DebugSession") as session:
        diagnosis = await run_agent(
            state,
            RetryProvider(),
            transport=httpx.MockTransport(recover),
            tracer=tracer,
        )
    shutdown_tracing(tracer_provider)

    tools = [
        span
        for span in exporter.get_finished_spans()
        if span.attributes.get(SpanAttributes.OPENINFERENCE_SPAN_KIND)
        == OpenInferenceSpanKindValues.TOOL.value
    ]
    assert diagnosis.status == "INCONCLUSIVE"
    assert attempts == state.tool_call_count == len(tools) == 2
    assert all(span.parent.span_id == session.get_span_context().span_id for span in tools)
    assert tools[0].status.status_code is StatusCode.ERROR
    assert tools[0].attributes[ERROR_TYPE] == "TOOL_TIMEOUT"
    assert tools[1].status.status_code is StatusCode.UNSET
    assert tools[1].attributes["apilens.tool.success"] is True
    assert "do-not-trace-this" not in repr([span.attributes for span in tools])


@pytest.mark.asyncio
async def test_interrupted_tool_execution_is_marked_as_session_timeout(
    monkeypatch,
) -> None:
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)
    exporter = RecordingExporter()
    tracer_provider = configure_tracing(span_exporter=exporter)
    assert tracer_provider is not None

    async def slow_execution(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(
        "agent_service.agent.dispatch.execute_api_request", slow_execution
    )
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.01):
            await dispatch_tool_call(
                ToolCallDecision(
                    kind="tool_call",
                    tool_name="execute_api_request",
                    arguments={"method": "GET", "path": "/products/1"},
                ),
                tracer=get_tracer(tracer_provider),
            )
    shutdown_tracing(tracer_provider)

    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["execute_api_request"]
    assert spans[0].status.status_code is StatusCode.ERROR
    assert spans[0].attributes[ERROR_TYPE] == "SESSION_TIMEOUT"
