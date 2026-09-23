import pytest
from pydantic import ValidationError

from agent_service.agent.schemas import Observation, ToolCallRecord
from agent_service.agent.state import AgentLimits, DebugSessionState, SessionStatus


def test_new_session_has_explicit_independent_initial_state() -> None:
    first = DebugSessionState(session_id="session-1", issue="POST /orders fails")
    second = DebugSessionState(session_id="session-2", issue="GET /orders/1 fails")

    assert first.step_count == 0
    assert first.tool_call_count == 0
    assert first.tool_history == []
    assert first.observations == []
    assert first.request_ids == []
    assert first.status is SessionStatus.RUNNING

    first.tool_history.append(
        ToolCallRecord(tool_name="inspect_api_spec", arguments={"path": "/orders"})
    )
    assert second.tool_history == []


def test_bookkeeping_helpers_only_update_owned_state() -> None:
    state = DebugSessionState(session_id="session-1", issue="POST /orders fails")
    call = ToolCallRecord(tool_name="execute_api_request", arguments={"path": "/orders"})
    observation = Observation(
        tool_name="execute_api_request",
        success=True,
        data={"status_code": 500},
        request_id="request-1",
    )

    state.record_decision()
    assert state.step_count == 1
    assert state.tool_call_count == 0

    state.record_tool_result(call, observation)
    state.record_tool_result(call, observation)
    assert state.step_count == 1
    assert state.tool_call_count == 2
    assert state.tool_history == [call, call]
    assert state.observations == [observation, observation]
    assert state.request_ids == ["request-1"]
    assert state.status is SessionStatus.RUNNING

    state.mark_completed()
    assert state.status is SessionStatus.COMPLETED


def test_tool_result_bookkeeping_rejects_mismatched_records() -> None:
    state = DebugSessionState(session_id="session-1", issue="POST /orders fails")
    call = ToolCallRecord(tool_name="inspect_api_spec", arguments={})
    observation = Observation(
        tool_name="execute_api_request",
        success=True,
        data={"status_code": 200},
    )

    with pytest.raises(ValueError):
        state.record_tool_result(call, observation)
    assert state.tool_call_count == 0
    assert state.tool_history == []
    assert state.observations == []


def test_limits_have_approved_defaults_and_reject_non_positive_values() -> None:
    limits = AgentLimits()
    assert limits.model_dump() == {
        "max_agent_decisions": 8,
        "max_tool_calls": 6,
        "max_identical_tool_calls": 2,
        "session_timeout_seconds": 45,
    }

    for field in AgentLimits.model_fields:
        with pytest.raises(ValidationError):
            AgentLimits.model_validate({field: 0})
