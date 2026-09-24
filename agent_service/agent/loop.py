import asyncio
import json
from enum import StrEnum
from typing import Protocol

import httpx

from agent_service.agent.dispatch import (
    ToolDispatchResult,
    canonical_call_identity,
    dispatch_tool_call,
    prepare_tool_call,
)
from agent_service.agent.schemas import (
    AgentDecision,
    DebugDiagnosis,
    EvidenceItem,
    EvidenceSource,
    FinalAnswerDecision,
    Observation,
    ToolCallRecord,
)
from agent_service.agent.state import AgentLimits, DebugSessionState
from sandbox_api.db import Database


class DecisionProvider(Protocol):
    async def next_decision(self, state: DebugSessionState) -> AgentDecision: ...


class DecisionProviderError(Exception):
    def __init__(self, error_type: str, message: str) -> None:
        self.error_type = error_type
        super().__init__(message)


class LoopFeedbackType(StrEnum):
    TOOL_CALL_LIMIT_REACHED = "TOOL_CALL_LIMIT_REACHED"
    IDENTICAL_TOOL_CALL_LIMIT_REACHED = "IDENTICAL_TOOL_CALL_LIMIT_REACHED"
    SESSION_TIMEOUT = "SESSION_TIMEOUT"


EVIDENCE_SOURCE_BY_TOOL = {
    "inspect_api_spec": EvidenceSource.API_SPEC,
    "execute_api_request": EvidenceSource.API_EXECUTION,
    "inspect_server_logs": EvidenceSource.SERVER_LOGS,
    "inspect_endpoint_implementation": EvidenceSource.ENDPOINT_IMPLEMENTATION,
}


def _feedback(tool_name: str, error_type: LoopFeedbackType, message: str) -> Observation:
    return Observation(
        tool_name=tool_name,
        success=False,
        error={"type": error_type.value, "message": message},
    )


def _collected_evidence(state: DebugSessionState) -> list[EvidenceItem]:
    return [
        EvidenceItem(
            source=EVIDENCE_SOURCE_BY_TOOL[observation.tool_name],
            finding=json.dumps(observation.data, ensure_ascii=False, sort_keys=True),
            reference=observation.request_id,
        )
        for observation in state.observations
        if observation.success and observation.tool_name in EVIDENCE_SOURCE_BY_TOOL
    ]


async def run_agent(
    state: DebugSessionState,
    provider: DecisionProvider,
    *,
    limits: AgentLimits | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    database: Database | None = None,
) -> DebugDiagnosis:
    limits = limits or AgentLimits()
    pending_call: ToolCallRecord | None = None

    try:
        async with asyncio.timeout(limits.session_timeout_seconds):
            while state.step_count < limits.max_agent_decisions:
                try:
                    decision = await provider.next_decision(state.model_copy(deep=True))
                except DecisionProviderError as error:
                    state.record_feedback(Observation(
                        tool_name="decision_provider",
                        success=False,
                        error={"type": error.error_type, "message": str(error)},
                    ))
                    state.mark_completed()
                    return DebugDiagnosis(
                        status="INCONCLUSIVE", missing_evidence=[str(error)]
                    )
                state.record_decision()

                if isinstance(decision, FinalAnswerDecision):
                    state.mark_completed()
                    return decision.diagnosis

                prepared = prepare_tool_call(decision)
                if isinstance(prepared, ToolDispatchResult):
                    state.record_feedback(prepared.observation)
                    continue

                identity = canonical_call_identity(prepared)
                if state.tool_call_count >= limits.max_tool_calls:
                    state.record_feedback(_feedback(
                        prepared.tool_name,
                        LoopFeedbackType.TOOL_CALL_LIMIT_REACHED,
                        "Tool-call limit reached",
                    ))
                    continue
                if sum(
                    canonical_call_identity(call) == identity
                    for call in state.tool_history
                ) >= limits.max_identical_tool_calls:
                    state.record_feedback(_feedback(
                        prepared.tool_name,
                        LoopFeedbackType.IDENTICAL_TOOL_CALL_LIMIT_REACHED,
                        "Identical tool-call limit reached",
                    ))
                    continue

                for attempt in range(2):
                    pending_call = prepared
                    result = await dispatch_tool_call(
                        prepared, transport=transport, database=database
                    )
                    pending_call = None
                    if not result.accepted:
                        state.record_feedback(result.observation)
                        break
                    state.record_tool_result(result.tool_call, result.observation)
                    timed_out = (
                        not result.observation.success
                        and result.observation.error["type"] == "TOOL_TIMEOUT"
                    )
                    if not timed_out or attempt == 1:
                        break
                    if state.tool_call_count >= limits.max_tool_calls:
                        break
                    if sum(
                        canonical_call_identity(call) == identity
                        for call in state.tool_history
                    ) >= limits.max_identical_tool_calls:
                        break
    except TimeoutError:
        if pending_call is not None:
            state.record_tool_result(
                pending_call,
                _feedback(
                    pending_call.tool_name,
                    LoopFeedbackType.SESSION_TIMEOUT,
                    "Session timed out during tool execution",
                ),
            )
        reason = "Session timeout reached"
    else:
        reason = "Agent decision limit reached"

    state.mark_completed()
    return DebugDiagnosis(
        status="LIMIT_REACHED",
        evidence=_collected_evidence(state),
        missing_evidence=[reason],
    )
