"""Supplementary observed-path metrics, never a substitute for correctness.

The shortest observed successful path is not a global optimum: shared waste can
remain, equally short alternate paths tie, and a single success scores one.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import Field

from agent_service.agent.dispatch import canonical_call_identity
from agent_service.agent.schemas import ToolCallDecision
from evaluation.contracts import (
    Contract,
    EvaluationRun,
    EvaluatorName,
    EvaluatorResult,
    GroundingLabel,
    ResultState,
    RootCauseLabel,
    SelectionLabel,
)
from evaluation.dataset import CaseKind, EvaluationCase


class PathStep(Contract):
    kind: Literal["decision", "tool"]
    reference: str
    decision_id: str
    tool_name: str | None = None
    timeout_retry: bool = False


class TrajectoryMetrics(Contract):
    decision_attempts: int = Field(ge=0)
    tool_attempts: int = Field(ge=0)
    path_length: int = Field(ge=0)
    duplicate_calls: int = Field(ge=0)
    non_retry_duplicate_calls: int = Field(ge=0)
    timeout_retries: int = Field(ge=0)
    unnecessary_tool_attempts: int = Field(ge=0)
    path: list[PathStep]


class SuccessEligibility(Contract):
    eligible: bool
    reason: str


class ConvergenceResult(Contract):
    case_id: str
    session_id: str
    ratio: float | None = Field(default=None, ge=0, le=1)
    reason: str
    shortest_successful_observed_path: int | None = Field(default=None, gt=0)


def _check_results(
    case: EvaluationCase, run: EvaluationRun, results: Sequence[EvaluatorResult]
) -> None:
    if case.case_id != run.case_id or any(
        result.case_id != run.case_id or result.session_id != run.session_id
        for result in results
    ):
        raise ValueError("case, run and evaluator result identifiers must agree")


def evaluate_trajectory(
    case: EvaluationCase,
    run: EvaluationRun,
    selection_results: Sequence[EvaluatorResult] = (),
) -> EvaluatorResult:
    _check_results(case, run, selection_results)
    unnecessary = {
        result.decision_id
        for result in selection_results
        if result.evaluator is EvaluatorName.TOOL_SELECTION
        and result.state is ResultState.SCORED
        and result.label == SelectionLabel.UNNECESSARY
    }
    seen: set[str] = set()
    duplicates = non_retry_duplicates = 0
    for attempt in run.tool_attempts:
        identity = canonical_call_identity(attempt.call)
        if identity in seen:
            duplicates += 1
            non_retry_duplicates += not attempt.timeout_retry
        seen.add(identity)
    path = []
    for decision in run.decisions:
        action = decision.decision
        path.append(PathStep(
            kind="decision",
            reference=decision.decision_id,
            decision_id=decision.decision_id,
            tool_name=action.tool_name if isinstance(action, ToolCallDecision) else None,
        ))
        path.extend(
            PathStep(
                kind="tool",
                reference=attempt.attempt_id,
                decision_id=decision.decision_id,
                tool_name=attempt.call.tool_name,
                timeout_retry=attempt.timeout_retry,
            )
            for attempt in run.tool_attempts
            if attempt.decision_id == decision.decision_id
        )
    metrics = TrajectoryMetrics(
        decision_attempts=len(run.decisions),
        tool_attempts=len(run.tool_attempts),
        path_length=len(run.decisions) + len(run.tool_attempts),
        duplicate_calls=duplicates,
        non_retry_duplicate_calls=non_retry_duplicates,
        timeout_retries=sum(attempt.timeout_retry for attempt in run.tool_attempts),
        unnecessary_tool_attempts=sum(
            attempt.decision_id in unnecessary for attempt in run.tool_attempts
        ),
        path=path,
    )
    return EvaluatorResult(
        evaluator=EvaluatorName.TRAJECTORY,
        unit="run",
        case_id=run.case_id,
        session_id=run.session_id,
        trace_id=run.trace_id,
        span_id=run.session_span_id,
        state=ResultState.SCORED,
        value=metrics.model_dump(mode="json"),
        explanation="Observed decision attempts plus actual tool attempts; retries are reported separately.",
    )


def assess_success(
    case: EvaluationCase,
    run: EvaluationRun,
    results: Sequence[EvaluatorResult],
) -> SuccessEligibility:
    _check_results(case, run, results)
    if run.diagnosis is None:
        return SuccessEligibility(eligible=False, reason="MISSING_DIAGNOSIS")
    if run.diagnosis.status != case.expected_final_status:
        return SuccessEligibility(eligible=False, reason="FINAL_STATUS_MISMATCH")
    required = {EvaluatorName.GROUNDING: GroundingLabel.GROUNDED}
    if case.case_kind is CaseKind.BUG:
        required[EvaluatorName.ROOT_CAUSE] = RootCauseLabel.CORRECT
    for name, label in required.items():
        matches = [result for result in results if result.evaluator is name]
        if len(matches) != 1 or matches[0].state is not ResultState.SCORED:
            return SuccessEligibility(eligible=False, reason=f"{name.value.upper()}_UNDETERMINED")
        if matches[0].label != label:
            return SuccessEligibility(eligible=False, reason=f"{name.value.upper()}_NOT_SUCCESSFUL")
    return SuccessEligibility(eligible=True, reason="SUCCESSFUL")


def convergence(
    cases_by_id: Mapping[str, EvaluationCase],
    runs: Sequence[EvaluationRun],
    results_by_session: Mapping[str, Sequence[EvaluatorResult]],
) -> list[ConvergenceResult]:
    """Compare only eligible runs in exactly matching observed cohorts."""
    if len({run.session_id for run in runs}) != len(runs):
        raise ValueError("duplicate session identifiers")
    cohorts: dict[str, list[int]] = {}
    keys: dict[str, str] = {}
    output = []
    for run in runs:
        case = cases_by_id[run.case_id]
        results = results_by_session.get(run.session_id, ())
        eligibility = assess_success(case, run, results)
        reason = eligibility.reason
        path_length = len(run.decisions) + len(run.tool_attempts)
        if eligibility.eligible:
            if not case.paraphrase_group:
                reason = "NO_PARAPHRASE_GROUP"
            elif run.comparison is None:
                reason = "MISSING_COMPARISON_CONTEXT"
            elif run.comparison.fixture_id != case.fixture_id:
                reason = "FIXTURE_MISMATCH"
            elif any(
                call.model != run.comparison.model for call in run.model_calls or []
            ):
                reason = "MODEL_MISMATCH"
            elif path_length == 0:
                reason = "EMPTY_PATH"
            else:
                versions = sorted({
                    (result.evaluator.value, result.version, result.judge_model or "")
                    for result in results
                })
                key = json.dumps(
                    [case.paraphrase_group, run.comparison.model_dump(mode="json"), versions],
                    sort_keys=True,
                )
                keys[run.session_id] = key
                cohorts.setdefault(key, []).append(path_length)
                reason = "SUCCESSFUL_OBSERVED_COHORT"
        output.append(ConvergenceResult(
            case_id=run.case_id, session_id=run.session_id, reason=reason
        ))
    for run, result in zip(runs, output, strict=True):
        if run.session_id in keys:
            shortest = min(cohorts[keys[run.session_id]])
            result.shortest_successful_observed_path = shortest
            result.ratio = shortest / (len(run.decisions) + len(run.tool_attempts))
    return output
