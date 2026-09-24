import asyncio
import json
from enum import StrEnum
from typing import Protocol

import httpx
from openinference.semconv.trace import (
    OpenInferenceSpanKindValues,
    SpanAttributes,
    ToolCallAttributes,
)
from opentelemetry.semconv.attributes.error_attributes import ERROR_TYPE
from opentelemetry.trace import Span, Status, StatusCode, Tracer

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
from agent_service.observability import get_tracer
from sandbox_api.db import Database

MAX_TRACE_VALUE_CHARS = 256


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


def _mark_decision_error(span: Span, error_type: str, message: str) -> None:
    span.set_attribute(ERROR_TYPE, error_type[:MAX_TRACE_VALUE_CHARS])
    span.set_status(Status(StatusCode.ERROR, message[:MAX_TRACE_VALUE_CHARS]))


def _record_selected_tool(span: Span, tool_call: ToolCallRecord) -> None:
    span.set_attribute(
        ToolCallAttributes.TOOL_CALL_FUNCTION_NAME,
        tool_call.tool_name[:MAX_TRACE_VALUE_CHARS],
    )
    safe_arguments = {
        key: value[:MAX_TRACE_VALUE_CHARS]
        for key in ("method", "path", "request_id")
        if isinstance((value := tool_call.arguments.get(key)), str)
    }
    if safe_arguments:
        span.set_attribute(
            ToolCallAttributes.TOOL_CALL_FUNCTION_ARGUMENTS_JSON,
            json.dumps(safe_arguments, sort_keys=True, separators=(",", ":")),
        )


async def run_agent(
    state: DebugSessionState,
    provider: DecisionProvider,
    *,
    limits: AgentLimits | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    database: Database | None = None,
    tracer: Tracer | None = None,
) -> DebugDiagnosis:
    limits = limits or AgentLimits()
    tracer = tracer if tracer is not None else get_tracer(None)
    pending_call: ToolCallRecord | None = None

    try:
        async with asyncio.timeout(limits.session_timeout_seconds):
            while state.step_count < limits.max_agent_decisions:
                with tracer.start_as_current_span(
                    "agent_decision",
                    attributes={
                        SpanAttributes.OPENINFERENCE_SPAN_KIND: (
                            OpenInferenceSpanKindValues.AGENT.value
                        ),
                        SpanAttributes.SESSION_ID: state.session_id[
                            :MAX_TRACE_VALUE_CHARS
                        ],
                        "apilens.decision.step_number": state.step_count + 1,
                    },
                    record_exception=False,
                    set_status_on_exception=False,
                ) as decision_span:
                    try:
                        decision = await provider.next_decision(
                            state.model_copy(deep=True)
                        )
                    except DecisionProviderError as error:
                        _mark_decision_error(
                            decision_span, error.error_type, str(error)
                        )
                        state.record_feedback(Observation(
                            tool_name="decision_provider",
                            success=False,
                            error={"type": error.error_type, "message": str(error)},
                        ))
                        state.mark_completed()
                        return DebugDiagnosis(
                            status="INCONCLUSIVE", missing_evidence=[str(error)]
                        )
                    except Exception as error:
                        _mark_decision_error(
                            decision_span,
                            type(error).__name__,
                            "Decision provider failed",
                        )
                        raise
                    state.record_decision()

                    if isinstance(decision, FinalAnswerDecision):
                        decision_span.set_attribute(
                            "apilens.decision.selected_action", "FINAL_ANSWER"
                        )
                        state.mark_completed()
                        return decision.diagnosis

                    decision_span.set_attribute(
                        "apilens.decision.selected_action", "TOOL_CALL"
                    )
                    decision_span.set_attribute(
                        ToolCallAttributes.TOOL_CALL_FUNCTION_NAME,
                        decision.tool_name[:MAX_TRACE_VALUE_CHARS],
                    )
                    prepared = prepare_tool_call(decision)
                    if isinstance(prepared, ToolDispatchResult):
                        error = prepared.observation.error
                        _mark_decision_error(
                            decision_span,
                            str(error["type"]),
                            str(error["message"]),
                        )
                        state.record_feedback(prepared.observation)
                        continue

                    _record_selected_tool(decision_span, prepared)
                    identity = canonical_call_identity(prepared)
                    if state.tool_call_count >= limits.max_tool_calls:
                        feedback = _feedback(
                            prepared.tool_name,
                            LoopFeedbackType.TOOL_CALL_LIMIT_REACHED,
                            "Tool-call limit reached",
                        )
                        _mark_decision_error(
                            decision_span,
                            LoopFeedbackType.TOOL_CALL_LIMIT_REACHED,
                            "Tool-call limit reached",
                        )
                        state.record_feedback(feedback)
                        continue
                    if sum(
                        canonical_call_identity(call) == identity
                        for call in state.tool_history
                    ) >= limits.max_identical_tool_calls:
                        feedback = _feedback(
                            prepared.tool_name,
                            LoopFeedbackType.IDENTICAL_TOOL_CALL_LIMIT_REACHED,
                            "Identical tool-call limit reached",
                        )
                        _mark_decision_error(
                            decision_span,
                            LoopFeedbackType.IDENTICAL_TOOL_CALL_LIMIT_REACHED,
                            "Identical tool-call limit reached",
                        )
                        state.record_feedback(feedback)
                        continue

                for attempt in range(2):
                    pending_call = prepared
                    result = await dispatch_tool_call(
                        prepared,
                        transport=transport,
                        database=database,
                        tracer=tracer,
                        session_id=state.session_id,
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
