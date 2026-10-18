"""Opt-in memory recorder. Snapshots are private evaluation artifacts, not traces."""

from time import monotonic

from opentelemetry.trace import get_current_span

from agent_service.agent.schemas import (
    AgentDecision,
    DebugDiagnosis,
    Observation,
    ToolCallRecord,
)
from agent_service.agent.state import AgentLimits, DebugSessionState
from evaluation.contracts import (
    ComparisonContext,
    DecisionRecord,
    EvaluationRun,
    ModelCall,
    RecordedObservation,
    RunError,
    TokenUsage,
    ToolAttempt,
)


def span_ids() -> tuple[str | None, str | None]:
    context = get_current_span().get_span_context()
    if not context.is_valid:
        return None, None
    return f"{context.trace_id:032x}", f"{context.span_id:016x}"


class EvaluationRecorder:
    def __init__(self, case_id: str, *, comparison: ComparisonContext | None = None):
        self.case_id = case_id
        self.comparison = comparison
        self._run: EvaluationRun | None = None
        self._started_at: float | None = None

    def start(self, state: DebugSessionState, limits: AgentLimits) -> None:
        if self._run is not None:
            raise ValueError("recorder belongs to one run")
        self._started_at = monotonic()
        trace_id, span_id = span_ids()
        if self.comparison is not None and self.comparison.limits != limits:
            raise ValueError("comparison limits differ from actual agent limits")
        self._run = EvaluationRun(
            case_id=self.case_id,
            session_id=state.session_id,
            trace_id=trace_id,
            session_span_id=span_id,
            comparison=self.comparison,
        )
        if trace_id is not None:
            get_current_span().set_attribute(
                "apilens.evaluation.case_id", self.case_id[:256]
            )

    @property
    def run(self) -> EvaluationRun:
        if self._run is None:
            raise ValueError("recorder has not started")
        return self._run

    def _sync(self, state: DebugSessionState) -> None:
        self.run.observations = [
            RecordedObservation(
                observation_id=f"observation-{index + 1}",
                observation=item.model_copy(deep=True),
            )
            for index, item in enumerate(state.observations)
        ]

    def decision_started(self, state: DebugSessionState) -> None:
        self._sync(state)
        self.run.decisions.append(
            DecisionRecord(
                decision_id=f"decision-{len(self.run.decisions) + 1}",
                prior_observation_ids=[
                    item.observation_id for item in self.run.observations
                ],
                span_id=span_ids()[1],
            )
        )

    def decision_returned(self, decision: AgentDecision) -> None:
        self.run.decisions[-1].decision = decision.model_copy(deep=True)

    def tool_started(self, call: ToolCallRecord, retry: bool) -> None:
        self.run.tool_attempts.append(
            ToolAttempt(
                attempt_id=f"tool-{len(self.run.tool_attempts) + 1}",
                decision_id=self.run.decisions[-1].decision_id,
                call=call.model_copy(deep=True),
                timeout_retry=retry,
            )
        )

    def tool_span(self) -> None:
        self.run.tool_attempts[-1].span_id = span_ids()[1]

    def tool_finished(self, observation: Observation) -> None:
        observation_id = f"observation-{len(self.run.observations) + 1}"
        self.run.observations.append(
            RecordedObservation(
                observation_id=observation_id,
                observation=observation.model_copy(deep=True),
            )
        )
        self.run.tool_attempts[-1].observation_id = observation_id
        if observation.error:
            self.error("tool", str(observation.error["type"]))

    def error(self, scope: str, error_type: str) -> None:
        self.run.errors.append(
            RunError(
                scope=scope,
                error_type=error_type,
                decision_id=self.run.decisions[-1].decision_id
                if self.run.decisions
                else None,
                tool_attempt_id=self.run.tool_attempts[-1].attempt_id
                if scope == "tool" and self.run.tool_attempts
                else None,
            )
        )

    def finish(
        self, state: DebugSessionState, diagnosis: DebugDiagnosis | None
    ) -> None:
        self._sync(state)
        self.run.diagnosis = diagnosis.model_copy(deep=True) if diagnosis else None
        self.run.latency_seconds = monotonic() - self._started_at

    def model_started(self, model: str) -> str:
        if self.run.model_calls is None:
            self.run.model_calls = []
        call = ModelCall(
            call_id=f"model-{len(self.run.model_calls) + 1}",
            decision_id=self.run.decisions[-1].decision_id
            if self.run.decisions else None,
            model=model,
        )
        self.run.model_calls.append(call)
        return call.call_id

    def model_finished(
        self,
        call_id: str,
        latency_seconds: float,
        usage: dict[str, int | None] | None,
        error_type: str | None,
    ) -> None:
        call = next(item for item in self.run.model_calls if item.call_id == call_id)
        call.latency_seconds = latency_seconds
        call.usage = TokenUsage(**usage) if usage is not None else None
        call.error_type = error_type
        if error_type is not None:
            self.error("model", error_type)

    def snapshot(self) -> EvaluationRun:
        return EvaluationRun.model_validate(
            self.run.model_dump(mode="python")
        ).model_copy(deep=True)
