"""Metrics are observations of recorder events, never estimates from decisions."""

import socket
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import APIConnectionError
from pydantic import ValidationError

from agent_service.agent import OpenAIDecisionProvider
from agent_service.agent.recording import current_recorder
from agent_service.agent.schemas import (
    DebugDiagnosis,
    FinalAnswerDecision,
    ToolCallDecision,
)
from agent_service.agent.state import AgentLimits, DebugSessionState
from agent_service.demo import DemoExecutionMetrics, DemoRecorder
from agent_service.main import create_app, run_debug_session


def final():
    return FinalAnswerDecision(kind="final_answer", diagnosis=DebugDiagnosis(
        status="INCONCLUSIVE", missing_evidence=["More information is needed"],
    ))


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Metrics tests must not open network connections")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "")


def test_duration_is_measured_only_between_lifecycle_hooks(monkeypatch):
    clock = iter([30.0, 32.5])
    monkeypatch.setattr("agent_service.demo.monotonic", lambda: next(clock))
    recorder = DemoRecorder()
    state = DebugSessionState(session_id="private", issue="private")
    assert recorder.metrics.duration_seconds is None
    recorder.start(state, AgentLimits())
    recorder.finish(state, final().diagnosis)
    assert recorder.metrics.model_dump() == {
        "duration_seconds": 2.5, "tool_attempt_count": 0, "model_request_count": None,
        "input_tokens": None, "output_tokens": None, "total_tokens": None,
    }


@pytest.mark.parametrize("usages,expected", [
    ([], (None, None, None)),
    ([None], (None, None, None)),
    ([{"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}], (0, 0, 0)),
    ([{"input_tokens": 10, "output_tokens": 3, "total_tokens": 13}] * 2, (20, 6, 26)),
    ([{"input_tokens": 10, "output_tokens": 3, "total_tokens": 13}, None], (None, None, None)),
    ([{"input_tokens": 10, "output_tokens": None, "total_tokens": None}], (10, None, None)),
    ([{"input_tokens": True, "output_tokens": -1, "total_tokens": "private"}], (None, None, None)),
])
def test_usage_is_complete_per_field_or_unavailable(usages, expected):
    recorder = DemoRecorder()
    state = DebugSessionState(session_id="private", issue="private")
    recorder.start(state, AgentLimits())
    for usage in usages:
        call_id = recorder.model_started("private-model")
        recorder.model_finished(call_id, 123.0, usage, "private-error")
    recorder.finish(state, final().diagnosis)
    metrics = recorder.metrics
    assert metrics.model_request_count == (len(usages) or None)
    assert (metrics.input_tokens, metrics.output_tokens, metrics.total_tokens) == expected
    assert "private" not in metrics.model_dump_json()
    assert "latency_seconds" not in metrics.model_dump()


def test_started_but_unfinished_model_request_prevents_partial_totals():
    recorder = DemoRecorder()
    call = recorder.model_started("private")
    recorder.model_finished(call, 1.0, {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}, None)
    recorder.model_started("private")
    recorder.finish(DebugSessionState(session_id="s", issue="report"), None)
    assert recorder.metrics.model_request_count == 2
    assert recorder.metrics.total_tokens is None
    assert recorder.metrics.duration_seconds is None


@pytest.mark.asyncio
async def test_tool_attempt_count_includes_timeout_retry_and_failure_but_not_rejection():
    decisions = iter([
        ToolCallDecision(kind="tool_call", tool_name="unknown", arguments={}),
        ToolCallDecision(kind="tool_call", tool_name="execute_api_request", arguments={"method": "GET", "path": "/products/1"}),
        final(),
    ])
    class Scripted:
        async def next_decision(self, state):
            return next(decisions)
    attempts = 0
    def http(request):
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("private", request=request)
    recorder = DemoRecorder()
    await run_debug_session("report", Scripted, recorder=recorder, transport=httpx.MockTransport(http))
    assert attempts == recorder.metrics.tool_attempt_count == 2
    assert [step.kind for step in recorder.steps] == ["rejected_action", "tool_attempt", "tool_attempt"]
    assert recorder.steps[-1].retry is True
    assert recorder.metrics.model_request_count is None


@pytest.mark.parametrize("with_error", [False, True])
@pytest.mark.asyncio
async def test_real_provider_hooks_count_corrections_and_errors_without_extra_decisions(with_error):
    replies = iter([
        SimpleNamespace(id="invalid", status="incomplete", output=[], usage=SimpleNamespace(input_tokens=2, output_tokens=1, total_tokens=3)),
        APIConnectionError(request=httpx.Request("POST", "http://offline")) if with_error else
        SimpleNamespace(id="final", status="completed", output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text")])], output_parsed=final().diagnosis.model_dump(mode="json"), usage=SimpleNamespace(input_tokens=4, output_tokens=2, total_tokens=6)),
    ])
    requests = []
    class Responses:
        async def parse(self, **kwargs):
            requests.append(kwargs)
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply
    recorder = DemoRecorder()
    result = await run_debug_session("report", lambda: OpenAIDecisionProvider(client=SimpleNamespace(responses=Responses())), recorder=recorder)
    assert len(requests) == recorder.metrics.model_request_count == 2
    assert recorder.metrics.total_tokens == (None if with_error else 9)
    assert len(recorder.steps) == (1 if with_error else 0)
    assert result.status == "INCONCLUSIVE"


def test_demo_returns_metrics_and_original_debug_does_not():
    class Measured:
        async def next_decision(self, state):
            recorder = current_recorder.get()
            if recorder is not None:
                call = recorder.model_started("private")
                recorder.model_finished(call, 1.0, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, None)
            return final()
    with TestClient(create_app(Measured, tracing_factory=lambda: None)) as client:
        demo = client.post("/demo/debug", json={"issue": "report"})
        regular = client.post("/debug", json={"issue": "report"})
    assert demo.status_code == 200
    assert demo.json()["metrics"]["model_request_count"] == 1
    assert demo.json()["metrics"]["total_tokens"] == 0
    assert demo.json()["diagnosis"] == regular.json()
    assert "metrics" not in regular.json() and "private" not in demo.text


@pytest.mark.parametrize("field,value", [
    ("duration_seconds", -1.0), ("duration_seconds", float("inf")),
    ("tool_attempt_count", True), ("model_request_count", -1),
    ("total_tokens", 1.5), ("input_tokens", "private"), ("output_tokens", -1),
])
def test_metrics_schema_rejects_invalid_measurements(field, value):
    with pytest.raises(ValidationError):
        DemoExecutionMetrics.model_validate({field: value})
