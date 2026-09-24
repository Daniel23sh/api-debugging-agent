import json
from uuid import UUID

import httpx
from fastapi.testclient import TestClient
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
    ToolCallDecision,
)
from agent_service.main import create_app
from agent_service.observability import (
    DEFAULT_PROJECT_NAME,
    DEFAULT_SERVICE_NAME,
    configure_tracing,
    get_tracer,
    shutdown_tracing,
)


class RecordingExporter(InMemorySpanExporter):
    def __init__(self) -> None:
        super().__init__()
        self.shutdown_count = 0

    def shutdown(self) -> None:
        self.shutdown_count += 1
        super().shutdown()


def test_tracing_is_disabled_without_a_collector(monkeypatch) -> None:
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)

    assert configure_tracing() is None
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

    for _ in range(2):
        app = create_app(tracing_factory=tracing_factory)
        with TestClient(app):
            assert app.state.tracer_provider is providers[-1]

    assert providers[0] is not providers[1]
    assert trace.get_tracer_provider() is global_provider
    assert [exporter.shutdown_count for exporter in exporters] == [1, 1]


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

    def respond(request: httpx.Request) -> httpx.Response:
        execution_parent_ids.append(
            trace.get_current_span().get_span_context().span_id
        )
        return httpx.Response(200, json={"accepted": True})

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
    final = next(span for span in spans if span.name == "final_diagnosis")

    assert diagnosis.status == "RESOLVED"
    assert exporter.shutdown_count == 1
    assert len(spans) == 5
    assert len(decisions) == provider.calls == 3
    assert all(span.context.trace_id == session.context.trace_id for span in spans)
    assert session.parent is None
    assert all(span.parent.span_id == session.context.span_id for span in decisions)
    assert final.parent.span_id == session.context.span_id
    assert all(
        span.attributes[SpanAttributes.OPENINFERENCE_SPAN_KIND]
        == OpenInferenceSpanKindValues.AGENT.value
        for span in decisions
    )
    assert provider.decision_span_ids == [span.context.span_id for span in decisions]
    assert execution_parent_ids == [session.context.span_id]

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
    assert "execute_api_request" not in {span.name for span in spans}
