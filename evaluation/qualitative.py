"""Independent semantic rubrics with intentionally different evidence inputs."""

from agent_service.agent.schemas import DiagnosisStatus
from evaluation.contracts import (
    EvaluationRun,
    EvaluatorName,
    EvaluatorResult,
    GroundingLabel,
    QualityLabel,
    ResultState,
    RootCauseLabel,
)
from evaluation.dataset import CaseKind, EvaluationCase
from evaluation.judges import JudgeFailure, JudgeProvider, request_judgment

ROOT_CAUSE_RUBRIC = """Compare causal mechanisms, not wording. CORRECT means the same
mechanism and essential triggering conditions. PARTIALLY_CORRECT identifies part of
the cause but misses a material causal detail. INCORRECT means a contradictory cause,
only a symptom, or no cause. Do not reward response style. References must be empty."""

GROUNDING_RUBRIC = """Judge the diagnosis against the user report and actual gathered
observations. Self-reported diagnosis evidence is a claim, not independent evidence.
GROUNDED: all material claims are supported. PARTIALLY_GROUNDED: minor claims lack
support. UNGROUNDED: a central root cause lacks support, or a tool execution/evidence
was invented. For INCONCLUSIVE/LIMIT_REACHED check that missing-information claims
are supported too. Cite only supplied observation IDs or user_report. A central
unsupported claim must not be excused by a correct-looking conclusion.
Grounding measures support for claims, not completeness, usefulness or whether the
agent could have reached a better final status. An accurately reported runtime
failure can be GROUNDED even when the existing evidence could answer the report.
Application failure observations are authoritative execution evidence, unlike the
diagnosis's own evidence claims. MODEL_OUTPUT_VALIDATION_ERROR means the application
exhausted its one corrective attempt without receiving an acceptable decision;
this supports a diagnosis reporting failure after correction. Do not demand raw
provider bodies or hidden model reasoning to corroborate that structured error."""

QUALITY_RUBRIC = """Assess clarity, usefulness, internal consistency, and whether the
suggested fix aligns with the claimed diagnosis. GOOD is clear and actionable for
its status; NEEDS_IMPROVEMENT has a material clarity/usefulness gap; POOR is confusing,
unhelpful or internally contradictory. A justified inconclusive answer can be GOOD.
Do not judge technical truth or evidence grounding: a clear but technically wrong
answer may be GOOD, and a correct but unusable answer may be POOR. References must
be empty. No expected root cause is supplied.
NEEDS_IMPROVEMENT still conveys some useful domain explanation or an actionable
next step, despite a material gap. POOR includes a response consisting only of an
internal failure message, with neither a useful domain explanation nor actionable
missing information/next steps. Accurately admitting an internal failure does not
by itself make the answer useful. Keep this independent of factual grounding."""


def _result(run: EvaluationRun, evaluator: EvaluatorName, **fields) -> EvaluatorResult:
    return EvaluatorResult(
        evaluator=evaluator,
        unit="run",
        case_id=run.case_id,
        session_id=run.session_id,
        trace_id=run.trace_id,
        span_id=run.session_span_id,
        version="2"
        if evaluator in {EvaluatorName.GROUNDING, EvaluatorName.RESPONSE_QUALITY}
        else "1",
        **fields,
    )


def _check(case: EvaluationCase, run: EvaluationRun) -> None:
    if case.case_id != run.case_id:
        raise ValueError("Evaluation case/run identifiers do not match")


def _missing(run: EvaluationRun, evaluator: EvaluatorName) -> EvaluatorResult:
    return _result(
        run,
        evaluator,
        state=ResultState.UNDETERMINED,
        explanation="No final diagnosis was recorded",
    )


async def _judge(run, evaluator, judge, rubric, data, label_type, evidence_ids):
    answer = await request_judgment(
        judge,
        evaluator=evaluator,
        rubric=rubric,
        data=data,
        label_type=label_type,
        evidence_ids=evidence_ids,
        require_evidence=evaluator is EvaluatorName.GROUNDING,
    )
    if isinstance(answer, JudgeFailure):
        return _result(
            run,
            evaluator,
            source="judge",
            state=ResultState.ERROR,
            error_type=answer.error_type,
            explanation=answer.explanation,
            judge_model=getattr(judge, "model", None),
        )
    return _result(
        run,
        evaluator,
        source="judge",
        state=ResultState.SCORED,
        label=answer.label,
        explanation=answer.explanation,
        evidence_refs=answer.evidence_refs,
        judge_model=getattr(judge, "model", None),
    )


async def evaluate_root_cause(
    case: EvaluationCase, run: EvaluationRun, judge: JudgeProvider
) -> EvaluatorResult:
    _check(case, run)
    evaluator = EvaluatorName.ROOT_CAUSE
    diagnosis = run.diagnosis
    if diagnosis is None:
        return _missing(run, evaluator)
    if case.case_kind is not CaseKind.BUG:
        if diagnosis.root_cause or diagnosis.status is DiagnosisStatus.RESOLVED:
            return _result(
                run,
                evaluator,
                state=ResultState.SCORED,
                label=RootCauseLabel.INCORRECT,
                explanation="The diagnosis asserts a bug for a non-bug case",
            )
        return _result(
            run,
            evaluator,
            state=ResultState.NOT_APPLICABLE,
            explanation="This case has no expected root cause",
        )
    if not diagnosis.root_cause:
        return _result(
            run,
            evaluator,
            state=ResultState.SCORED,
            label=RootCauseLabel.INCORRECT,
            explanation="A bug case has no diagnosed root cause",
        )
    return await _judge(
        run,
        evaluator,
        judge,
        ROOT_CAUSE_RUBRIC,
        {
            "user_report": case.user_report,
            "endpoint": case.endpoint,
            "expected_root_cause": case.expected_root_cause,
            "actual_root_cause": diagnosis.root_cause,
        },
        RootCauseLabel,
        set(),
    )


def collected_evidence(run: EvaluationRun) -> list[dict]:
    # Tool failures may contain transport/provider details: retain only their type.
    evidence = []
    for item in run.observations:
        observation = item.observation
        evidence.append(
            {
                "observation_id": item.observation_id,
                "tool_name": observation.tool_name,
                "success": observation.success,
                "request_id": observation.request_id,
                "data": _without_credentials(observation.data)
                if observation.tool_name
                in {
                    "inspect_api_spec",
                    "execute_api_request",
                    "inspect_server_logs",
                    "inspect_endpoint_implementation",
                }
                else None,
                "error_type": (observation.error or {}).get("type"),
            }
        )
    return evidence


def _without_credentials(value):
    if isinstance(value, dict):
        return {
            key: _without_credentials(item)
            for key, item in value.items()
            if key.lower().replace("-", "_")
            not in {
                "headers",
                "authorization",
                "api_key",
                "access_token",
                "refresh_token",
                "password",
                "secret",
                "credentials",
                "cookie",
            }
        }
    if isinstance(value, list):
        return [_without_credentials(item) for item in value]
    return value


async def evaluate_grounding(
    case: EvaluationCase, run: EvaluationRun, judge: JudgeProvider
) -> EvaluatorResult:
    _check(case, run)
    evaluator = EvaluatorName.GROUNDING
    if run.diagnosis is None:
        return _missing(run, evaluator)
    return await _judge(
        run,
        evaluator,
        judge,
        GROUNDING_RUBRIC,
        {
            "user_report": case.user_report,
            "diagnosis": run.diagnosis.model_dump(mode="json"),
            "observations": collected_evidence(run),
        },
        GroundingLabel,
        {"user_report", *(item.observation_id for item in run.observations)},
    )


async def evaluate_response_quality(
    case: EvaluationCase, run: EvaluationRun, judge: JudgeProvider
) -> EvaluatorResult:
    _check(case, run)
    evaluator = EvaluatorName.RESPONSE_QUALITY
    if run.diagnosis is None:
        return _missing(run, evaluator)
    return await _judge(
        run,
        evaluator,
        judge,
        QUALITY_RUBRIC,
        {
            "user_report": case.user_report,
            "diagnosis": run.diagnosis.model_dump(mode="json"),
        },
        QualityLabel,
        set(),
    )
