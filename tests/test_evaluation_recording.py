import asyncio

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError

from agent_service.agent import DebugDiagnosis, FinalAnswerDecision, ToolCallDecision
from agent_service.agent.loop import DecisionProviderError, run_agent
from agent_service.agent.state import DebugSessionState
from agent_service.main import run_debug_session
from agent_service.observability import configure_tracing, get_tracer, shutdown_tracing
from evaluation.contracts import EvaluationRun, EvaluatorResult
from evaluation.recorder import EvaluationRecorder


def tool(name="execute_api_request", **arguments):
    return ToolCallDecision(kind="tool_call", tool_name=name, arguments=arguments)


def final():
    return FinalAnswerDecision(
        kind="final_answer",
        diagnosis=DebugDiagnosis(
            status="INCONCLUSIVE", missing_evidence=["more information"]
        ),
    )


class Scripted:
    def __init__(self, *decisions):
        self.decisions = iter(decisions)

    async def next_decision(self, state):
        return next(self.decisions)


@pytest.mark.asyncio
async def test_record_actual_attempts_rejections_and_prior_context():
    recorder = EvaluationRecorder("case")
    requests = []

    def http(request):
        requests.append(request)
        return httpx.Response(
            422, json={"detail": "validation evidence"}, headers={"X-Request-ID": "r-1"}
        )

    state = DebugSessionState(session_id="s", issue="quantity issue")
    result = await run_agent(
        state,
        Scripted(
            tool("unknown"),
            tool(method="POST", path="/orders", body={"quantity": 0}),
            final(),
        ),
        recorder=recorder,
        transport=httpx.MockTransport(http),
    )
    run = recorder.snapshot()
    assert result == run.diagnosis
    assert len(run.decisions) == 3 and len(run.tool_attempts) == 1
    assert run.decisions[0].prior_observation_ids == []
    assert run.decisions[1].prior_observation_ids == ["observation-1"]
    assert run.decisions[2].prior_observation_ids == ["observation-1", "observation-2"]
    assert run.tool_attempts[0].decision_id == "decision-2"
    assert run.tool_attempts[0].observation_id == "observation-2"
    assert run.observations[1].observation.success  # HTTP 422 is evidence
    assert len(requests) == 1 and state.step_count == 3
    assert run.trace_id is None and run.decisions[0].span_id is None
    assert run.model_calls is None and run.latency_seconds >= 0
    state.observations[0].error["message"] = "mutated"
    recorder.run.decisions.clear()
    assert len(run.decisions) == 3
    assert run.observations[0].observation.error["message"] != "mutated"
    assert EvaluationRun.model_validate_json(run.model_dump_json()) == run


@pytest.mark.asyncio
async def test_timeout_retry_is_one_decision_two_attempts():
    calls = 0

    def http(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("private", request=request)
        return httpx.Response(200, json={"id": 1})

    recorder = EvaluationRecorder("case")
    await run_agent(
        DebugSessionState(session_id="s", issue="product"),
        Scripted(tool(method="GET", path="/products/1"), final()),
        recorder=recorder,
        transport=httpx.MockTransport(http),
    )
    run = recorder.snapshot()
    assert [item.timeout_retry for item in run.tool_attempts] == [False, True]
    assert {item.decision_id for item in run.tool_attempts} == {"decision-1"}
    assert run.decisions[1].prior_observation_ids == ["observation-1", "observation-2"]
    assert run.errors[0].error_type == "TOOL_TIMEOUT"


@pytest.mark.asyncio
async def test_failed_decision_is_recorded_without_incrementing_counter():
    class Failed:
        async def next_decision(self, state):
            raise DecisionProviderError("MODEL_API_ERROR", "OpenAI API request failed")

    recorder = EvaluationRecorder("case")
    state = DebugSessionState(session_id="s", issue="issue")
    await run_agent(state, Failed(), recorder=recorder)
    run = recorder.snapshot()
    assert state.step_count == 0 and len(run.decisions) == 1
    assert run.decisions[0].decision is None
    assert run.errors[0].error_type == "MODEL_API_ERROR"
    assert run.diagnosis.status == "INCONCLUSIVE"


@pytest.mark.asyncio
async def test_parallel_session_recorders_and_trace_links_are_isolated():
    exporter = InMemorySpanExporter()
    provider = configure_tracing(span_exporter=exporter)
    recorders = [EvaluationRecorder("case-a"), EvaluationRecorder("case-b")]
    try:
        await asyncio.gather(
            *[
                run_debug_session(
                    "private issue",
                    lambda: Scripted(
                        tool(
                            "inspect_endpoint_implementation",
                            method="GET",
                            path="/products/1",
                        ),
                        final(),
                    ),
                    recorder=recorder,
                    tracer=get_tracer(provider),
                )
                for recorder in recorders
            ]
        )
        runs = [recorder.snapshot() for recorder in recorders]
        assert runs[0].session_id != runs[1].session_id
        assert runs[0].trace_id != runs[1].trace_id
        assert all(
            run.session_span_id
            and run.decisions[0].span_id
            and run.tool_attempts[0].span_id
            for run in runs
        )
        assert "private issue" not in str(
            [span.attributes for span in exporter.get_finished_spans()]
        )
        assert {run.case_id for run in runs} == {"case-a", "case-b"}
    finally:
        shutdown_tracing(provider)


def test_result_states_and_label_contracts():
    base = {
        "evaluator": "root_cause",
        "unit": "run",
        "case_id": "c",
        "session_id": "s",
        "explanation": "insufficient information",
    }
    with pytest.raises(ValidationError):
        EvaluatorResult(**base, state="UNDETERMINED", label="INCORRECT")
    with pytest.raises(ValidationError):
        EvaluatorResult(**base, state="SCORED", label="GROUNDED")
    with pytest.raises(ValidationError):
        EvaluatorResult(**base, state="ERROR")
    assert EvaluatorResult(**base, state="NOT_APPLICABLE").label is None


@pytest.mark.asyncio
async def test_snapshot_is_stable_while_agent_continues():
    entered = asyncio.Event()
    release = asyncio.Event()

    class Provider:
        async def next_decision(self, state):
            if not state.observations:
                return tool(
                    "inspect_endpoint_implementation", method="GET", path="/products/1"
                )
            entered.set()
            await release.wait()
            return final()

    recorder = EvaluationRecorder("case")
    task = asyncio.create_task(
        run_agent(
            DebugSessionState(session_id="s", issue="issue"),
            Provider(),
            recorder=recorder,
        )
    )
    await entered.wait()
    snapshot = recorder.snapshot()
    frozen = snapshot.model_dump_json()
    assert snapshot.diagnosis is None and snapshot.decisions[-1].decision is None
    release.set()
    await task
    assert recorder.snapshot().diagnosis is not None
    assert snapshot.model_dump_json() == frozen


@pytest.mark.asyncio
async def test_cancelled_provider_keeps_attempt_without_private_exception():
    entered = asyncio.Event()

    class Provider:
        async def next_decision(self, state):
            entered.set()
            await asyncio.Event().wait()

    recorder = EvaluationRecorder("case")
    task = asyncio.create_task(
        run_agent(
            DebugSessionState(session_id="s", issue="PRIVATE_ISSUE"),
            Provider(),
            recorder=recorder,
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    run = recorder.snapshot()
    assert run.diagnosis is None and len(run.decisions) == 1
    assert run.errors[0].error_type == "CANCELLED"
    assert "PRIVATE_ISSUE" not in str(run.errors)


def test_snapshot_cannot_link_a_decision_to_its_own_future_result():
    from agent_service.agent.schemas import Observation, ToolCallRecord
    from evaluation.contracts import DecisionRecord, RecordedObservation, ToolAttempt

    with pytest.raises(ValidationError, match="future observation"):
        EvaluationRun(
            case_id="c",
            session_id="s",
            decisions=[DecisionRecord(decision_id="d", prior_observation_ids=["o"])],
            observations=[
                RecordedObservation(
                    observation_id="o",
                    observation=Observation(
                        tool_name="inspect_api_spec", success=True, data={}
                    ),
                )
            ],
            tool_attempts=[
                ToolAttempt(
                    attempt_id="t",
                    decision_id="d",
                    observation_id="o",
                    call=ToolCallRecord(tool_name="inspect_api_spec", arguments={}),
                )
            ],
        )
