import asyncio
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI

from agent_service.agent.loop import run_agent
from agent_service.agent.openai_provider import OpenAIDecisionProvider
from agent_service.agent.schemas import (
    DebugDiagnosis,
    FinalAnswerDecision,
    ToolCallDecision,
)
from agent_service.agent.state import DebugSessionState
from evaluation.contracts import EvaluationRun, ModelCall, TokenUsage
from evaluation.metrics import collect_system_metrics
from evaluation.recorder import EvaluationRecorder


def response(*, valid=True, usage=None):
    return SimpleNamespace(
        id="r", status="completed", usage=usage,
        output=[SimpleNamespace(
            type="message", content=[SimpleNamespace(type="output_text")]
        )] if valid else [],
        output_parsed=DebugDiagnosis(
            status="INCONCLUSIVE", missing_evidence=["endpoint needed"]
        ).model_dump(),
    )


@pytest.mark.asyncio
async def test_actual_sdk_requests_count_corrective_retry_and_partial_usage():
    replies = iter([
        response(valid=False, usage=SimpleNamespace(
            input_tokens=10, output_tokens=3, total_tokens=13,
            input_tokens_details=SimpleNamespace(cached_tokens=4),
            output_tokens_details=SimpleNamespace(reasoning_tokens=2),
        )),
        response(usage=SimpleNamespace(input_tokens=5)),
    ])

    async def parse(**kwargs):
        return next(replies)

    recorder = EvaluationRecorder("case")
    await run_agent(
        DebugSessionState(session_id="s", issue="issue"),
        OpenAIDecisionProvider(
            client=SimpleNamespace(responses=SimpleNamespace(parse=parse)),
            model="test-model",
        ),
        recorder=recorder,
    )
    run = recorder.snapshot()
    metrics = collect_system_metrics(run)
    assert len(run.decisions) == 1
    assert metrics.model_calls == 2 and metrics.tool_calls == 0
    assert metrics.final_status == "INCONCLUSIVE"
    assert {call.decision_id for call in run.model_calls} == {"decision-1"}
    assert all(call.latency_seconds >= 0 for call in run.model_calls)
    assert metrics.latency_seconds >= sum(call.latency_seconds for call in run.model_calls)
    assert metrics.token_usage.input_tokens == 15
    assert metrics.token_usage.output_tokens is None
    assert metrics.token_usage.total_tokens is None
    assert run.model_calls[0].usage.total_tokens == 13
    assert run.model_calls[0].usage.cached_input_tokens == 4


@pytest.mark.asyncio
async def test_mocked_sdk_http_error_records_safe_class_and_actual_call_count():
    marker = "PRIVATE_PROVIDER_BODY_DO_NOT_STORE"
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(400, json={"error": {"message": marker}})

    recorder = EvaluationRecorder("case")
    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client,
        AsyncOpenAI(api_key="test", max_retries=0, http_client=http_client) as client,
    ):
        await run_agent(
            DebugSessionState(session_id="s", issue="issue"),
            OpenAIDecisionProvider(client=client, model="test-model"),
            recorder=recorder,
        )
    run = recorder.snapshot()
    metrics = collect_system_metrics(run)
    assert len(requests) == metrics.model_calls == 1
    assert len(run.decisions) == 1
    assert run.model_calls[0].error_type == "BadRequestError"
    assert metrics.error_counts["BadRequestError"] == 1
    assert metrics.error_counts["MODEL_API_ERROR"] == 1
    assert run.model_calls[0].usage is None
    assert marker not in run.model_dump_json()
    assert marker not in metrics.model_dump_json()


@pytest.mark.asyncio
async def test_unobserved_provider_calls_are_missing_and_postrun_calls_are_excluded():
    class Scripted:
        async def next_decision(self, state):
            return FinalAnswerDecision(
                kind="final_answer",
                diagnosis=DebugDiagnosis(
                    status="INCONCLUSIVE", missing_evidence=["endpoint needed"]
                ),
            )

    recorder = EvaluationRecorder("case")
    await run_agent(
        DebugSessionState(session_id="s", issue="issue"), Scripted(), recorder=recorder
    )

    async def parse(**kwargs):
        return response()

    # No active agent recorder after run_agent returns (including later judges).
    await OpenAIDecisionProvider(
        client=SimpleNamespace(responses=SimpleNamespace(parse=parse)), model="test"
    ).next_decision(DebugSessionState(session_id="other", issue="other"))
    metrics = collect_system_metrics(recorder.snapshot())
    assert metrics.model_calls is None and metrics.token_usage is None
    assert metrics.latency_seconds >= 0


def test_usage_subsets_are_not_added_to_token_totals():
    run = EvaluationRun(
        case_id="c", session_id="s", model_calls=[
            ModelCall(call_id="m", model="test", usage=TokenUsage(
                input_tokens=10, output_tokens=5, total_tokens=15,
                cached_input_tokens=4, reasoning_output_tokens=3,
            )),
        ],
    )
    metrics = collect_system_metrics(run)
    assert metrics.token_usage.total_tokens == 15
    assert metrics.token_usage.input_tokens == 10
    assert metrics.token_usage.output_tokens == 5
    assert metrics.token_usage.cached_input_tokens == 4
    assert metrics.token_usage.reasoning_output_tokens == 3
    assert collect_system_metrics(run.model_copy(update={"model_calls": []})).model_calls == 0


def test_provider_total_without_component_usage_stays_incomplete():
    run = EvaluationRun(
        case_id="c", session_id="s", model_calls=[
            ModelCall(call_id="m", model="test", usage=TokenUsage(total_tokens=15)),
        ],
    )
    assert run.model_calls[0].usage.total_tokens == 15
    assert collect_system_metrics(run).token_usage.total_tokens is None


@pytest.mark.asyncio
async def test_actual_tool_timeout_retry_counts_attempts_and_structured_errors():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("private", request=request)
        return httpx.Response(200, json={"id": 1})

    class Scripted:
        def __init__(self):
            self.first = True

        async def next_decision(self, state):
            if self.first:
                self.first = False
                return ToolCallDecision(
                    kind="tool_call", tool_name="execute_api_request",
                    arguments={"method": "GET", "path": "/products/1"},
                )
            return FinalAnswerDecision(
                kind="final_answer",
                diagnosis=DebugDiagnosis(
                    status="INCONCLUSIVE", missing_evidence=["missing"]
                ),
            )

    recorder = EvaluationRecorder("case")
    await run_agent(
        DebugSessionState(session_id="s", issue="issue"), Scripted(), recorder=recorder,
        transport=httpx.MockTransport(handler),
    )
    run = recorder.snapshot()
    metrics = collect_system_metrics(run)
    assert calls == metrics.tool_calls == 2
    assert [attempt.timeout_retry for attempt in run.tool_attempts] == [False, True]
    assert metrics.model_calls is None
    assert metrics.error_counts == {"TOOL_TIMEOUT": 1}


@pytest.mark.asyncio
async def test_interrupted_sdk_request_retains_actual_attempt_and_safe_error():
    started = asyncio.Event()

    async def parse(**kwargs):
        started.set()
        await asyncio.Event().wait()

    recorder = EvaluationRecorder("case")
    task = asyncio.create_task(run_agent(
        DebugSessionState(session_id="s", issue="private issue"),
        OpenAIDecisionProvider(
            client=SimpleNamespace(responses=SimpleNamespace(parse=parse)), model="test"
        ),
        recorder=recorder,
    ))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    run = recorder.snapshot()
    assert run.model_calls[0].error_type == "CancelledError"
    assert run.model_calls[0].latency_seconds >= 0
    assert run.latency_seconds >= 0
    assert run.diagnosis is None
    assert collect_system_metrics(run).model_calls == 1
    assert "private issue" not in run.model_dump_json()
