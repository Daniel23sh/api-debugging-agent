from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from agent_service.agent.schemas import NonEmptyStr, Observation, ToolCallRecord


class SessionStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"


class DebugSessionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: NonEmptyStr
    issue: NonEmptyStr
    step_count: int = Field(default=0, ge=0)
    tool_call_count: int = Field(default=0, ge=0)
    tool_history: list[ToolCallRecord] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    request_ids: list[str] = Field(default_factory=list)
    status: SessionStatus = SessionStatus.RUNNING

    def record_decision(self) -> None:
        self.step_count += 1

    def record_feedback(self, observation: Observation) -> None:
        if observation.success:
            raise ValueError("feedback must be a failed observation")
        self.observations.append(observation)

    def record_tool_result(
        self,
        tool_call: ToolCallRecord,
        observation: Observation,
    ) -> None:
        if tool_call.tool_name != observation.tool_name:
            raise ValueError("tool call and observation names must match")
        self.tool_call_count += 1
        self.tool_history.append(tool_call)
        self.observations.append(observation)
        if observation.request_id and observation.request_id not in self.request_ids:
            self.request_ids.append(observation.request_id)

    def mark_completed(self) -> None:
        self.status = SessionStatus.COMPLETED


class AgentLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_agent_decisions: PositiveInt = 8
    max_tool_calls: PositiveInt = 6
    max_identical_tool_calls: PositiveInt = 2
    session_timeout_seconds: PositiveInt = 45
