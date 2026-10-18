"""Optional in-process recording hooks, separate from agent state and telemetry."""

from contextvars import ContextVar
from typing import Protocol

from agent_service.agent.schemas import (
    AgentDecision,
    DebugDiagnosis,
    Observation,
    ToolCallRecord,
)
from agent_service.agent.state import AgentLimits, DebugSessionState


class SessionRecorder(Protocol):
    def start(self, state: DebugSessionState, limits: AgentLimits) -> None: ...
    def decision_started(self, state: DebugSessionState) -> None: ...
    def decision_returned(self, decision: AgentDecision) -> None: ...
    def tool_started(self, call: ToolCallRecord, retry: bool) -> None: ...
    def tool_span(self) -> None: ...
    def tool_finished(self, observation: Observation) -> None: ...
    def error(self, scope: str, error_type: str) -> None: ...
    def model_started(self, model: str) -> str: ...
    def model_finished(
        self,
        call_id: str,
        latency_seconds: float,
        usage: dict[str, int | None] | None,
        error_type: str | None,
    ) -> None: ...
    def finish(
        self, state: DebugSessionState, diagnosis: DebugDiagnosis | None
    ) -> None: ...


current_recorder: ContextVar[SessionRecorder | None] = ContextVar(
    "apilens_session_recorder", default=None
)
