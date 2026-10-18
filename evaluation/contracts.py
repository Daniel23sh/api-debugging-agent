"""Evaluation snapshots describe observations, never prescribe a trajectory."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from agent_service.agent.schemas import (
    AgentDecision,
    DebugDiagnosis,
    NonEmptyStr,
    Observation,
    ToolCallRecord,
)
from agent_service.agent.state import AgentLimits


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RunError(Contract):
    scope: Literal["session", "decision", "tool", "model"]
    error_type: NonEmptyStr
    decision_id: str | None = None
    tool_attempt_id: str | None = None


class DecisionRecord(Contract):
    decision_id: NonEmptyStr
    prior_observation_ids: list[str] = Field(default_factory=list)
    decision: AgentDecision | None = None
    span_id: str | None = None


class ToolAttempt(Contract):
    attempt_id: NonEmptyStr
    decision_id: NonEmptyStr
    call: ToolCallRecord
    timeout_retry: bool = False
    observation_id: str | None = None
    span_id: str | None = None


class RecordedObservation(Contract):
    observation_id: NonEmptyStr
    observation: Observation


class TokenUsage(Contract):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_output_tokens: int | None = Field(default=None, ge=0)


class ModelCall(Contract):
    call_id: NonEmptyStr
    decision_id: str | None = None
    model: str
    latency_seconds: float | None = Field(default=None, ge=0)
    usage: TokenUsage | None = None
    error_type: str | None = None


class ComparisonContext(Contract):
    dataset_version: NonEmptyStr
    fixture_id: NonEmptyStr
    agent_version: NonEmptyStr
    model: NonEmptyStr
    model_settings: dict[str, str] = Field(default_factory=dict)
    limits: AgentLimits = Field(default_factory=AgentLimits)


class EvaluationRun(Contract):
    case_id: NonEmptyStr
    session_id: NonEmptyStr
    trace_id: str | None = None
    session_span_id: str | None = None
    diagnosis: DebugDiagnosis | None = None
    decisions: list[DecisionRecord] = Field(default_factory=list)
    tool_attempts: list[ToolAttempt] = Field(default_factory=list)
    observations: list[RecordedObservation] = Field(default_factory=list)
    errors: list[RunError] = Field(default_factory=list)
    model_calls: list[ModelCall] | None = None
    latency_seconds: float | None = Field(default=None, ge=0)
    comparison: ComparisonContext | None = None

    @model_validator(mode="after")
    def validate_links(self) -> "EvaluationRun":
        observations = {item.observation_id for item in self.observations}
        decisions = {item.decision_id for item in self.decisions}
        if len(observations) != len(self.observations) or len(decisions) != len(
            self.decisions
        ):
            raise ValueError("duplicate snapshot identifiers")
        attempts = {item.attempt_id for item in self.tool_attempts}
        if len(attempts) != len(self.tool_attempts):
            raise ValueError("duplicate tool attempt identifiers")
        for item in self.decisions:
            if not set(item.prior_observation_ids) <= observations:
                raise ValueError("unknown prior observation")
        for item in self.tool_attempts:
            if item.decision_id not in decisions or (
                item.observation_id is not None
                and item.observation_id not in observations
            ):
                raise ValueError("unknown tool attempt reference")
        decision_index = {
            item.decision_id: index for index, item in enumerate(self.decisions)
        }
        observation_order = [item.observation_id for item in self.observations]
        previous: list[str] = []
        for index, item in enumerate(self.decisions):
            if (
                item.prior_observation_ids
                != observation_order[: len(item.prior_observation_ids)]
            ):
                raise ValueError("prior observations must be an ordered prefix")
            if item.prior_observation_ids[: len(previous)] != previous:
                raise ValueError("available observations cannot go backwards")
            for attempt in self.tool_attempts:
                if (
                    attempt.observation_id in item.prior_observation_ids
                    and decision_index[attempt.decision_id] >= index
                ):
                    raise ValueError("decision context contains a future observation")
            previous = item.prior_observation_ids
        for error in self.errors:
            if error.decision_id is not None and error.decision_id not in decisions:
                raise ValueError("unknown error decision reference")
            if (
                error.tool_attempt_id is not None
                and error.tool_attempt_id not in attempts
            ):
                raise ValueError("unknown error attempt reference")
        for call in self.model_calls or []:
            if call.decision_id is not None and call.decision_id not in decisions:
                raise ValueError("unknown model call decision reference")
        return self


class ResultState(StrEnum):
    SCORED = "SCORED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNDETERMINED = "UNDETERMINED"
    ERROR = "ERROR"


class EvaluatorName(StrEnum):
    TOOL_SELECTION = "tool_selection"
    TOOL_ARGUMENTS = "tool_arguments"
    ROOT_CAUSE = "root_cause"
    GROUNDING = "grounding"
    RESPONSE_QUALITY = "response_quality"
    TRAJECTORY = "trajectory"


class SelectionLabel(StrEnum):
    APPROPRIATE = "APPROPRIATE"
    UNNECESSARY = "UNNECESSARY"
    INVALID_TOOL = "INVALID_TOOL"


class ArgumentsLabel(StrEnum):
    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"


class RootCauseLabel(StrEnum):
    CORRECT = "CORRECT"
    PARTIALLY_CORRECT = "PARTIALLY_CORRECT"
    INCORRECT = "INCORRECT"


class GroundingLabel(StrEnum):
    GROUNDED = "GROUNDED"
    PARTIALLY_GROUNDED = "PARTIALLY_GROUNDED"
    UNGROUNDED = "UNGROUNDED"


class QualityLabel(StrEnum):
    GOOD = "GOOD"
    NEEDS_IMPROVEMENT = "NEEDS_IMPROVEMENT"
    POOR = "POOR"


class EvaluatorResult(Contract):
    evaluator: EvaluatorName
    version: NonEmptyStr = "1"
    unit: Literal["decision", "run"]
    case_id: NonEmptyStr
    session_id: NonEmptyStr
    trace_id: str | None = None
    span_id: str | None = None
    decision_id: str | None = None
    source: Literal["code", "judge"] = "code"
    judge_model: str | None = None
    state: ResultState
    label: (
        SelectionLabel
        | ArgumentsLabel
        | RootCauseLabel
        | GroundingLabel
        | QualityLabel
        | None
    ) = None
    value: dict[str, JsonValue] | None = None
    explanation: str = Field(max_length=500)
    evidence_refs: list[str] = Field(default_factory=list)
    error_type: str | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "EvaluatorResult":
        labels = {
            EvaluatorName.TOOL_SELECTION: SelectionLabel,
            EvaluatorName.TOOL_ARGUMENTS: ArgumentsLabel,
            EvaluatorName.ROOT_CAUSE: RootCauseLabel,
            EvaluatorName.GROUNDING: GroundingLabel,
            EvaluatorName.RESPONSE_QUALITY: QualityLabel,
        }
        if self.state is ResultState.SCORED:
            if self.evaluator in labels:
                if self.label is None:
                    raise ValueError("scored evaluator requires its label")
                self.label = labels[self.evaluator](self.label)
            elif self.value is None:
                raise ValueError("scored trajectory requires a value")
        elif self.label is not None or self.value is not None:
            raise ValueError("unscored results cannot carry a score")
        if (self.state is ResultState.ERROR) != (self.error_type is not None):
            raise ValueError("only evaluator errors require error_type")
        if (self.unit == "decision") != (self.decision_id is not None):
            raise ValueError("decision results require decision_id")
        return self
