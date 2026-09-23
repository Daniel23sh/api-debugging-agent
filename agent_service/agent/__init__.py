"""Provider-independent contracts and state for APILens investigations."""

from agent_service.agent.dispatch import (
    TOOL_REGISTRY,
    DispatchErrorType,
    ToolDispatchResult,
    canonical_call_identity,
    dispatch_tool_call,
    prepare_tool_call,
    registered_tool_schemas,
)
from agent_service.agent.loop import DecisionProvider, LoopFeedbackType, run_agent
from agent_service.agent.schemas import (
    AgentDecision,
    DebugDiagnosis,
    DiagnosisStatus,
    EvidenceItem,
    EvidenceSource,
    FinalAnswerDecision,
    Observation,
    ToolCallDecision,
    ToolCallRecord,
)
from agent_service.agent.state import AgentLimits, DebugSessionState, SessionStatus

__all__ = [
    "TOOL_REGISTRY",
    "AgentDecision",
    "AgentLimits",
    "DebugDiagnosis",
    "DebugSessionState",
    "DecisionProvider",
    "DiagnosisStatus",
    "DispatchErrorType",
    "EvidenceItem",
    "EvidenceSource",
    "FinalAnswerDecision",
    "LoopFeedbackType",
    "Observation",
    "SessionStatus",
    "ToolCallDecision",
    "ToolCallRecord",
    "ToolDispatchResult",
    "canonical_call_identity",
    "dispatch_tool_call",
    "prepare_tool_call",
    "registered_tool_schemas",
    "run_agent",
]
