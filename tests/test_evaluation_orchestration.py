import pytest

from agent_service.agent.schemas import DebugDiagnosis, Observation, ToolCallDecision
from evaluation.contracts import (
    ComparisonContext,
    DecisionRecord,
    EvaluationRun,
    EvaluatorName,
    RecordedObservation,
    ResultState,
)
from evaluation.dataset import load_evaluation_cases
from evaluation.judges import JudgeError
from evaluation.orchestration import EvaluationBundle, evaluate_run
from evaluation.trajectory import convergence

CASES = {
    case.case_id: case
    for case in load_evaluation_cases("evaluation/datasets/v1_cases.json")
}


class Judge:
    model = "mock-judge"

    def __init__(self, failed=None):
        self.requests = []
        self.failed = failed

    async def judge(self, request, response_type):
        self.requests.append(request)
        if request.evaluator == self.failed:
            raise JudgeError("JUDGE_API_ERROR")
        labels = {
            "root_cause": "CORRECT",
            "grounding": "GROUNDED",
            "response_quality": "GOOD",
            "tool_selection": "APPROPRIATE",
        }
        return response_type(
            label=labels[request.evaluator],
            explanation="Supported",
            evidence_refs=["user_report"]
            if request.evaluator in {"grounding", "tool_selection"}
            else [],
        )


def example_run():
    case = CASES["inventory_validation_zero_01"]
    run = EvaluationRun(
        case_id=case.case_id,
        session_id="s",
        trace_id="trace",
        session_span_id="session",
        decisions=[
            DecisionRecord(
                decision_id="d1",
                span_id="span-1",
                decision=ToolCallDecision(
                    kind="tool_call",
                    tool_name="inspect_endpoint_implementation",
                    arguments={"method": "PATCH", "path": "/inventory/1"},
                ),
            ),
            DecisionRecord(
                decision_id="d2",
                span_id="span-2",
                prior_observation_ids=["o1"],
                decision=ToolCallDecision(
                    kind="tool_call",
                    tool_name="inspect_api_spec",
                    arguments={"method": "WRONG", "path": "/"},
                ),
            ),
        ],
        observations=[
            RecordedObservation(
                observation_id="o1",
                observation=Observation(
                    tool_name="inspect_endpoint_implementation",
                    success=True,
                    data={
                        "sections": [{"source": "quantity > 0"}],
                        "future_marker": "NO_FUTURE_CONTEXT",
                    },
                ),
            )
        ],
        diagnosis=DebugDiagnosis(
            status="RESOLVED",
            affected_endpoint="PATCH /inventory/1",
            root_cause="gt=0 rejects zero",
            suggested_fix="ge=0",
            evidence=[{"source": "endpoint_implementation", "finding": "gt=0"}],
        ),
    )
    return case, run


@pytest.mark.asyncio
async def test_bundle_has_six_evaluators_and_links_without_changing_run():
    case, run = example_run()
    before = run.model_dump_json()
    judge = Judge()
    bundle = await evaluate_run(case, run, judge)
    assert {item.evaluator for item in bundle.results} == set(EvaluatorName)
    assert bundle.convergence_eligibility.eligible
    assert bundle.system_metrics.model_calls is None
    assert all(
        item.session_id == "s" and item.trace_id == "trace" for item in bundle.results
    )
    assert bundle.results[0].span_id == "span-1" and bundle.results[0].source == "judge"
    assert bundle.results[0].judge_model == "mock-judge"
    # Only unresolved selection invokes fallback; invalid parameters stay separate.
    selections = [item for item in judge.requests if item.evaluator == "tool_selection"]
    assert len(selections) == 1
    assert "NO_FUTURE_CONTEXT" not in selections[0].model_dump_json()
    assert "arguments" not in selections[0].data
    assert bundle.results[2].label == "APPROPRIATE"
    assert bundle.results[3].label == "INCORRECT"
    assert run.model_dump_json() == before
    assert EvaluationBundle.model_validate_json(bundle.model_dump_json()) == bundle


@pytest.mark.asyncio
async def test_evaluator_api_error_is_not_agent_failure_or_quality_zero():
    case, run = example_run()
    bundle = await evaluate_run(case, run, Judge(failed="grounding"))
    grounding = next(item for item in bundle.results if item.evaluator == "grounding")
    assert grounding.state is ResultState.ERROR
    assert grounding.error_type == "JUDGE_API_ERROR" and grounding.label is None
    assert not bundle.convergence_eligibility.eligible
    assert run.diagnosis.status == "RESOLVED" and bundle.system_metrics.errors == []
    assert (
        next(
            item for item in bundle.results if item.evaluator == "response_quality"
        ).label
        == "GOOD"
    )


@pytest.mark.asyncio
async def test_missing_decision_does_not_invoke_selection_judge():
    case = CASES["insufficient_report_no_endpoint"]
    run = EvaluationRun(
        case_id=case.case_id,
        session_id="s",
        decisions=[DecisionRecord(decision_id="failed")],
    )
    judge = Judge()
    bundle = await evaluate_run(case, run, judge)
    assert not judge.requests
    assert bundle.results[0].state is ResultState.UNDETERMINED
    assert not bundle.convergence_eligibility.eligible


@pytest.mark.asyncio
async def test_case_mismatch_rejected_before_any_judge_call():
    _case, run = example_run()
    judge = Judge()
    with pytest.raises(ValueError, match="identifiers"):
        await evaluate_run(CASES["order_total_quantity_02"], run, judge)
    assert not judge.requests


@pytest.mark.asyncio
async def test_same_pipeline_cohort_does_not_depend_on_using_selection_fallback():
    from agent_service.agent.schemas import FinalAnswerDecision

    case, ambiguous = example_run()
    ambiguous.comparison = ComparisonContext(
        dataset_version="v1",
        fixture_id=case.fixture_id,
        agent_version="source1",
        model="mock-agent",
    )
    # Same configured evaluator, different valid choice: one needs semantic review.
    shorter = ambiguous.model_copy(deep=True)
    shorter.session_id = "shorter"
    shorter.decisions[0].decision = ToolCallDecision(
        kind="tool_call",
        tool_name="inspect_api_spec",
        arguments={"method": "PATCH", "path": "/inventory/1"},
    )
    ambiguous.decisions.append(
        DecisionRecord(
            decision_id="d3",
            prior_observation_ids=["o1"],
            decision=FinalAnswerDecision(
                kind="final_answer", diagnosis=ambiguous.diagnosis
            ),
        )
    )
    first = await evaluate_run(case, ambiguous, Judge())
    second = await evaluate_run(case, shorter, Judge())
    results = convergence(
        {case.case_id: case},
        [ambiguous, shorter],
        {ambiguous.session_id: first.results, shorter.session_id: second.results},
    )
    assert results[0].ratio == pytest.approx(2 / 3)
    assert results[1].ratio == 1
