import asyncio
import time
from pathlib import Path

import httpx
import pytest

from agent_service.agent.loop import run_agent
from agent_service.agent.schemas import (
    DebugDiagnosis,
    FinalAnswerDecision,
    ToolCallDecision,
)
from agent_service.agent.state import AgentLimits, DebugSessionState, SessionStatus
from sandbox_api.db import Database
from sandbox_api.main import create_app


def tool(name: str, **arguments: object) -> ToolCallDecision:
    return ToolCallDecision(kind="tool_call", tool_name=name, arguments=arguments)


def final(status: str = "INCONCLUSIVE") -> FinalAnswerDecision:
    values = (
        {
            "status": "RESOLVED",
            "affected_endpoint": "GET /products/1",
            "root_cause": "Example root cause",
            "evidence": [{"source": "api_execution", "finding": "Observed response"}],
            "suggested_fix": "Example fix",
        }
        if status == "RESOLVED"
        else {"status": "NO_BUG_FOUND", "evidence": [
            {"source": "api_spec", "finding": "Contract matches behavior"}
        ]}
        if status == "NO_BUG_FOUND"
        else {"status": "INCONCLUSIVE", "missing_evidence": ["more context"]}
    )
    return FinalAnswerDecision(kind="final_answer", diagnosis=DebugDiagnosis(**values))


class ScriptedProvider:
    def __init__(self, *decisions: ToolCallDecision | FinalAnswerDecision) -> None:
        self.decisions = iter(decisions)
        self.snapshots = []

    async def next_decision(self, state: DebugSessionState):
        self.snapshots.append(state)
        return next(self.decisions)


@pytest.mark.asyncio
async def test_short_trajectories_and_final_statuses(tmp_path: Path) -> None:
    path = tmp_path / "sandbox.db"
    Database(path).initialize()
    transport = httpx.ASGITransport(app=create_app(path))
    first = DebugSessionState(session_id="1", issue="order failure")
    provider = ScriptedProvider(
        tool("inspect_api_spec", method="GET", path="/products/1"),
        final("NO_BUG_FOUND"),
    )
    diagnosis = await run_agent(first, provider, transport=transport)
    assert diagnosis.status == "NO_BUG_FOUND"
    assert first.step_count == 2 and first.tool_call_count == 1
    assert first.status is SessionStatus.COMPLETED
    assert provider.snapshots[1].observations[0].success

    second = DebugSessionState(session_id="2", issue="product lookup")
    provider = ScriptedProvider(
        tool("inspect_endpoint_implementation", method="GET", path="/products/1"),
        final(),
    )
    diagnosis = await run_agent(second, provider)
    assert diagnosis.status == "INCONCLUSIVE"
    assert second.tool_history[0].tool_name == "inspect_endpoint_implementation"

    resolved_state = DebugSessionState(session_id="3", issue="resolved")
    diagnosis = await run_agent(
        resolved_state,
        ScriptedProvider(tool("execute_api_request", method="GET", path="/products/1"), final("RESOLVED")),
        transport=transport,
    )
    assert diagnosis.status == "RESOLVED"
    assert resolved_state.status is SessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_state_aware_provider_uses_runtime_request_id(tmp_path: Path) -> None:
    path = tmp_path / "sandbox.db"
    database = Database(path)
    database.initialize()

    class StateAware:
        async def next_decision(self, snapshot: DebugSessionState):
            if not snapshot.observations:
                return tool("execute_api_request", method="GET", path="/products/1")
            if len(snapshot.observations) == 1:
                return tool("inspect_server_logs", request_id=snapshot.request_ids[0])
            return final()

    state = DebugSessionState(session_id="id", issue="trace product request")
    await run_agent(
        state, StateAware(), database=database,
        transport=httpx.ASGITransport(app=create_app(path)),
    )
    assert state.step_count == 3 and state.tool_call_count == 2
    assert state.observations[1].request_id == state.request_ids[0]
    assert state.tool_history[1].tool_name == "inspect_server_logs"


@pytest.mark.asyncio
async def test_provider_mutates_only_its_snapshot() -> None:
    class MutatingProvider:
        async def next_decision(self, snapshot: DebugSessionState):
            snapshot.step_count = 900
            snapshot.request_ids.append("invented")
            snapshot.issue = "changed"
            return final()

    state = DebugSessionState(session_id="id", issue="original")
    await run_agent(state, MutatingProvider())
    assert state.step_count == 1
    assert state.request_ids == [] and state.issue == "original"


@pytest.mark.asyncio
async def test_rejected_requests_record_feedback_without_execution() -> None:
    requests = []
    transport = httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(200))
    state = DebugSessionState(session_id="id", issue="bad calls")
    provider = ScriptedProvider(
        tool("unknown"),
        tool("execute_api_request", method="DELETE", path="/products/1"),
        final(),
    )
    await run_agent(state, provider, transport=transport)
    assert state.step_count == 3 and state.tool_call_count == 0
    assert state.tool_history == [] and requests == []
    assert [item.error["type"] for item in state.observations] == [
        "TOOL_NOT_FOUND", "TOOL_ARGUMENT_VALIDATION_ERROR"
    ]
    assert provider.snapshots[-1].observations == state.observations


@pytest.mark.asyncio
async def test_global_limit_blocks_tool_but_allows_later_final() -> None:
    requests = []
    transport = httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(200))
    state = DebugSessionState(session_id="id", issue="limit")
    provider = ScriptedProvider(
        tool("execute_api_request", method="GET", path="/products/1"),
        tool("execute_api_request", method="GET", path="/products/2"),
        final(),
    )
    diagnosis = await run_agent(
        state, provider, transport=transport,
        limits=AgentLimits(max_tool_calls=1),
    )
    assert diagnosis.status == "INCONCLUSIVE"
    assert len(requests) == state.tool_call_count == 1
    assert len(state.tool_history) == 1
    assert state.step_count == 3
    assert state.observations[-1].error["type"] == "TOOL_CALL_LIMIT_REACHED"
    assert provider.snapshots[-1].observations[-1].error["type"] == "TOOL_CALL_LIMIT_REACHED"


@pytest.mark.asyncio
async def test_normalized_duplicate_guard_blocks_third_call() -> None:
    requests = []
    transport = httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(200))
    state = DebugSessionState(session_id="id", issue="duplicates")
    provider = ScriptedProvider(
        tool("execute_api_request", method="get", path=" /products/1 "),
        tool("execute_api_request", path="/products/1", method="GET"),
        tool("execute_api_request", method="Get", path="/products/1"),
        final(),
    )
    await run_agent(state, provider, transport=transport)
    assert len(requests) == state.tool_call_count == 2
    assert len(state.tool_history) == 2
    assert state.step_count == 4
    assert state.tool_history[0].arguments == state.tool_history[1].arguments
    assert state.observations[-1].error["type"] == "IDENTICAL_TOOL_CALL_LIMIT_REACHED"


@pytest.mark.asyncio
async def test_timeout_retries_once_and_records_both_attempts() -> None:
    requests = []

    def timeout(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadTimeout("slow", request=request)

    state = DebugSessionState(session_id="id", issue="timeout")
    provider = ScriptedProvider(
        tool("execute_api_request", method="GET", path="/products/1"), final()
    )
    await run_agent(state, provider, transport=httpx.MockTransport(timeout))
    assert len(requests) == state.tool_call_count == 2
    assert state.step_count == 2
    assert len(state.tool_history) == len(state.observations) == 2
    assert [item.error["type"] for item in state.observations] == [
        "TOOL_TIMEOUT", "TOOL_TIMEOUT"
    ]


@pytest.mark.asyncio
async def test_timeout_retry_can_succeed_on_second_attempt() -> None:
    attempts = 0

    def recover(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json={"id": 1})

    state = DebugSessionState(session_id="id", issue="intermittent timeout")
    await run_agent(
        state,
        ScriptedProvider(tool("execute_api_request", method="GET", path="/products/1"), final()),
        transport=httpx.MockTransport(recover),
    )
    assert attempts == state.tool_call_count == 2
    assert state.step_count == 2
    assert [item.success for item in state.observations] == [False, True]


@pytest.mark.asyncio
@pytest.mark.parametrize("limit_kwargs", [
    {"max_tool_calls": 1}, {"max_identical_tool_calls": 1},
])
async def test_timeout_retry_respects_both_execution_limits(limit_kwargs: dict) -> None:
    requests = []

    def timeout(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadTimeout("slow", request=request)

    state = DebugSessionState(session_id="id", issue="timeout")
    await run_agent(
        state,
        ScriptedProvider(tool("execute_api_request", method="GET", path="/products/1"), final()),
        transport=httpx.MockTransport(timeout), limits=AgentLimits(**limit_kwargs),
    )
    assert len(requests) == state.tool_call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("body,status", [
    ({"product_id": 1, "quantity": 0}, 422),
    ({"product_id": 4, "quantity": 1}, 500),
])
async def test_http_error_status_does_not_retry(tmp_path: Path, body: dict, status: int) -> None:
    path = tmp_path / "sandbox.db"
    Database(path).initialize()
    state = DebugSessionState(session_id="id", issue="HTTP evidence")
    await run_agent(
        state,
        ScriptedProvider(tool("execute_api_request", method="POST", path="/orders", body=body), final()),
        transport=httpx.ASGITransport(app=create_app(path)),
    )
    assert state.tool_call_count == 1
    assert state.observations[0].success
    assert state.observations[0].data["status_code"] == status


@pytest.mark.asyncio
async def test_non_timeout_phase_two_failure_does_not_retry() -> None:
    state = DebugSessionState(session_id="id", issue="unsupported")
    await run_agent(
        state,
        ScriptedProvider(tool("inspect_api_spec", method="POST", path="/products/1"), final()),
    )
    assert state.tool_call_count == 1
    assert state.observations[0].error["type"] == "ENDPOINT_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_decision_limit_never_requests_extra_decision() -> None:
    provider = ScriptedProvider(
        tool("inspect_endpoint_implementation", method="GET", path="/products/1"),
        tool("unknown"),
    )
    state = DebugSessionState(session_id="id", issue="budget")
    diagnosis = await run_agent(
        state, provider, limits=AgentLimits(max_agent_decisions=2)
    )
    assert diagnosis.status == "LIMIT_REACHED"
    assert len(diagnosis.evidence) == 1
    assert diagnosis.evidence[0].source == "endpoint_implementation"
    assert '"path": "/products/{product_id}"' in diagnosis.evidence[0].finding
    assert diagnosis.missing_evidence == ["Agent decision limit reached"]
    assert state.step_count == len(provider.snapshots) == 2
    assert len(state.observations) == 2
    assert state.observations[0].success
    assert state.observations[1].error["type"] == "TOOL_NOT_FOUND"
    assert state.status is SessionStatus.COMPLETED

    final_state = DebugSessionState(session_id="final", issue="last decision")
    diagnosis = await run_agent(
        final_state, ScriptedProvider(tool("unknown"), final()),
        limits=AgentLimits(max_agent_decisions=2),
    )
    assert diagnosis.status == "INCONCLUSIVE"
    assert final_state.step_count == 2


@pytest.mark.asyncio
async def test_session_deadline_interrupts_awaited_provider() -> None:
    class HangingProvider:
        calls = 0

        async def next_decision(self, state: DebugSessionState):
            self.calls += 1
            if self.calls == 1:
                return tool(
                    "inspect_endpoint_implementation",
                    method="GET",
                    path="/products/1",
                )
            await asyncio.sleep(10)
            return final()

    provider = HangingProvider()
    state = DebugSessionState(session_id="id", issue="slow provider")
    diagnosis = await run_agent(
        state, provider, limits=AgentLimits(session_timeout_seconds=1)
    )
    assert diagnosis.status == "LIMIT_REACHED"
    assert len(diagnosis.evidence) == 1
    assert diagnosis.evidence[0].source == "endpoint_implementation"
    assert diagnosis.missing_evidence == ["Session timeout reached"]
    assert provider.calls == 2 and state.step_count == 1
    assert len(state.observations) == 1 and state.observations[0].success
    assert state.status is SessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_session_deadline_interrupts_awaited_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow_dispatch(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr("agent_service.agent.loop.dispatch_tool_call", slow_dispatch)
    state = DebugSessionState(session_id="id", issue="slow dispatch")
    diagnosis = await run_agent(
        state,
        ScriptedProvider(tool("execute_api_request", method="GET", path="/products/1")),
        limits=AgentLimits(session_timeout_seconds=1),
    )
    assert diagnosis.status == "LIMIT_REACHED"
    assert state.step_count == state.tool_call_count == 1
    assert state.observations[0].error["type"] == "SESSION_TIMEOUT"
    assert state.status is SessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_session_deadline_bounds_synchronous_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow_inspection(*args, **kwargs):
        time.sleep(2)

    monkeypatch.setattr(
        "agent_service.agent.dispatch.inspect_endpoint_implementation", slow_inspection
    )
    state = DebugSessionState(session_id="id", issue="slow sync tool")
    started = asyncio.get_running_loop().time()
    diagnosis = await run_agent(
        state,
        ScriptedProvider(tool("inspect_endpoint_implementation", method="GET", path="/products/1")),
        limits=AgentLimits(session_timeout_seconds=1),
    )
    assert asyncio.get_running_loop().time() - started < 1.8
    assert diagnosis.status == "LIMIT_REACHED"
    assert state.tool_call_count == 1
    assert state.observations[0].error["type"] == "SESSION_TIMEOUT"
