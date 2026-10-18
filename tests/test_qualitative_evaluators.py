from pathlib import Path

import pytest

from agent_service.agent.schemas import DebugDiagnosis, Observation
from evaluation.contracts import (
    EvaluationRun,
    GroundingLabel,
    QualityLabel,
    RecordedObservation,
    ResultState,
    RootCauseLabel,
)
from evaluation.dataset import load_evaluation_cases
from evaluation.qualitative import (
    collected_evidence,
    evaluate_grounding,
    evaluate_response_quality,
    evaluate_root_cause,
)

CASES = {
    case.case_id: case
    for case in load_evaluation_cases(
        Path(__file__).parents[1] / "evaluation/datasets/v1_cases.json"
    )
}
BUG = CASES["inventory_validation_zero_01"]


class Judge:
    def __init__(self, label, refs=None):
        self.label, self.refs = label, refs or []
        self.requests = []

    async def judge(self, request, response_type):
        self.requests.append(request)
        return response_type(
            label=self.label,
            explanation="Rubric-based assessment",
            evidence_refs=self.refs,
        )


def run(case=BUG, **changes):
    fields = {
        "case_id": case.case_id,
        "session_id": "s",
        "trace_id": "trace",
        "session_span_id": "root",
        "diagnosis": DebugDiagnosis(
            status="RESOLVED",
            affected_endpoint="/inventory/{product_id}",
            root_cause="Quantity rejects zero",
            suggested_fix="Use ge=0",
            evidence=[{"source": "api_spec", "finding": "minimum 0"}],
        ),
    }
    fields.update(changes)
    return EvaluationRun(**fields)


@pytest.mark.asyncio
@pytest.mark.parametrize("label", list(RootCauseLabel))
async def test_root_uses_semantic_judge_and_preserves_run_links(label):
    judge = Judge(label)
    result = await evaluate_root_cause(BUG, run(), judge)
    assert result.label is label
    assert result.state is ResultState.SCORED
    assert result.source == "judge"
    assert (result.case_id, result.session_id, result.trace_id, result.span_id) == (
        BUG.case_id,
        "s",
        "trace",
        "root",
    )
    data = judge.requests[0].data
    assert data["expected_root_cause"] == BUG.expected_root_cause
    assert data["actual_root_cause"] != data["expected_root_cause"]
    assert "observations" not in data and "suggested_fix" not in data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_id", ["order_create_status_no_bug", "insufficient_report_no_endpoint"]
)
async def test_non_bug_root_semantics_do_not_invoke_judge(case_id):
    case = CASES[case_id]
    judge = Judge("INCORRECT")
    diagnosis = DebugDiagnosis(status="INCONCLUSIVE", missing_evidence=["endpoint"])
    result = await evaluate_root_cause(case, run(case, diagnosis=diagnosis), judge)
    assert result.state is ResultState.NOT_APPLICABLE
    false_bug = await evaluate_root_cause(case, run(case), judge)
    assert false_bug.label is RootCauseLabel.INCORRECT
    assert not judge.requests


@pytest.mark.asyncio
async def test_missing_output_and_missing_bug_cause_are_distinct():
    judge = Judge("CORRECT")
    missing = await evaluate_root_cause(BUG, run(diagnosis=None), judge)
    assert missing.state is ResultState.UNDETERMINED
    no_cause = await evaluate_root_cause(
        BUG,
        run(
            diagnosis=DebugDiagnosis(status="INCONCLUSIVE", missing_evidence=["source"])
        ),
        judge,
    )
    assert no_cause.label is RootCauseLabel.INCORRECT
    assert not judge.requests


def observation(identifier="o1", **changes):
    fields = {
        "tool_name": "execute_api_request",
        "success": True,
        "data": {
            "method": "PATCH",
            "path": "/inventory/1",
            "status_code": 422,
            "response_body": {"detail": "quantity must be greater than 0"},
        },
    }
    fields.update(changes)
    return RecordedObservation(
        observation_id=identifier, observation=Observation(**fields)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("label", list(GroundingLabel))
async def test_grounding_uses_actual_observations_not_just_claimed_evidence(label):
    judge = Judge(label, ["o1"])
    result = await evaluate_grounding(BUG, run(observations=[observation()]), judge)
    assert result.label is label
    data = judge.requests[0].data
    assert data["observations"][0]["data"]["status_code"] == 422
    assert "diagnosis" in data and "user_report" in data
    assert "expected_root_cause" not in data
    assert result.evidence_refs == ["o1"]


@pytest.mark.asyncio
async def test_fabricated_evidence_and_justified_inconclusive():
    unsupported = Judge("UNGROUNDED", ["user_report"])
    result = await evaluate_grounding(BUG, run(), unsupported)
    assert result.label is GroundingLabel.UNGROUNDED
    assert unsupported.requests[0].data["observations"] == []
    case = CASES["insufficient_report_no_endpoint"]
    judge = Judge("GROUNDED", ["user_report"])
    result = await evaluate_grounding(
        case,
        run(
            case,
            diagnosis=DebugDiagnosis(
                status="INCONCLUSIVE",
                missing_evidence=["No endpoint or request was supplied"],
            ),
        ),
        judge,
    )
    assert result.label is GroundingLabel.GROUNDED


@pytest.mark.asyncio
@pytest.mark.parametrize("label", list(QualityLabel))
async def test_quality_excludes_ground_truth_and_is_independent_of_correctness(label):
    judge = Judge(label)
    result = await evaluate_response_quality(BUG, run(), judge)
    assert result.label is label
    assert set(judge.requests[0].data) == {"user_report", "diagnosis"}
    assert BUG.expected_root_cause not in judge.requests[0].model_dump_json()
    assert "technically wrong" in judge.requests[0].rubric


@pytest.mark.asyncio
async def test_correct_but_unhelpful_and_clear_but_wrong_remain_separate():
    snapshot = run()
    root = await evaluate_root_cause(BUG, snapshot, Judge("CORRECT"))
    quality = await evaluate_response_quality(BUG, snapshot, Judge("POOR"))
    assert root.label is RootCauseLabel.CORRECT and quality.label is QualityLabel.POOR
    root = await evaluate_root_cause(BUG, snapshot, Judge("INCORRECT"))
    quality = await evaluate_response_quality(BUG, snapshot, Judge("GOOD"))
    assert root.label is RootCauseLabel.INCORRECT and quality.label is QualityLabel.GOOD


@pytest.mark.asyncio
async def test_supported_application_failure_can_be_grounded_but_poor():
    case = CASES["order_create_status_no_bug"]
    snapshot = run(
        case,
        diagnosis=DebugDiagnosis(
            status="INCONCLUSIVE",
            missing_evidence=[
                "Model did not return one valid decision after correction"
            ],
        ),
        observations=[
            observation(
                tool_name="decision_provider",
                success=False,
                data=None,
                error={
                    "type": "MODEL_OUTPUT_VALIDATION_ERROR",
                    "message": "PRIVATE_PROVIDER_BODY",
                },
            )
        ],
    )
    grounding_judge = Judge("GROUNDED", ["o1"])
    quality_judge = Judge("POOR")
    grounded = await evaluate_grounding(case, snapshot, grounding_judge)
    quality = await evaluate_response_quality(case, snapshot, quality_judge)
    assert (
        grounded.label is GroundingLabel.GROUNDED and quality.label is QualityLabel.POOR
    )
    assert grounded.version == quality.version == "2"
    request = grounding_judge.requests[0]
    assert (
        request.data["observations"][0]["error_type"] == "MODEL_OUTPUT_VALIDATION_ERROR"
    )
    assert "exhausted its one corrective attempt" in request.rubric
    assert "not completeness, usefulness" in request.rubric
    assert "PRIVATE_PROVIDER_BODY" not in request.model_dump_json()
    assert "internal failure message" in quality_judge.requests[0].rubric
    assert "observations" not in quality_judge.requests[0].data


def test_projection_excludes_credentials_headers_and_failure_messages():
    snapshot = run(
        observations=[
            observation(
                data={
                    "response_body": {
                        "id": 1,
                        "api_key": "PRIVATE",
                        "nested": {
                            "headers": {"Authorization": "PRIVATE"},
                            "quantity": 0,
                        },
                    },
                    "headers": {"cookie": "PRIVATE"},
                }
            ),
            observation(
                "o2",
                success=False,
                data=None,
                error={
                    "type": "TOOL_HTTP_ERROR",
                    "message": "PRIVATE_PROVIDER_BODY",
                },
            ),
        ]
    )
    evidence = collected_evidence(snapshot)
    assert "PRIVATE" not in str(evidence)
    assert evidence[0]["data"]["response_body"]["nested"]["quantity"] == 0
    assert evidence[1]["error_type"] == "TOOL_HTTP_ERROR"


@pytest.mark.asyncio
async def test_judge_failure_is_not_agent_quality_failure():
    class FailingJudge:
        async def judge(self, *args):
            raise ValueError("PRIVATE_PROVIDER_BODY")

    result = await evaluate_grounding(BUG, run(), FailingJudge())
    assert result.state is ResultState.ERROR
    assert result.label is None
    assert "PRIVATE" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evaluate", [evaluate_root_cause, evaluate_grounding, evaluate_response_quality]
)
async def test_case_identity_is_validated_and_missing_diagnosis_not_scored(evaluate):
    with pytest.raises(ValueError, match="identifiers"):
        await evaluate(BUG, run(case_id="other"), Judge("CORRECT"))
    result = await evaluate(BUG, run(diagnosis=None), Judge("CORRECT"))
    assert result.state is ResultState.UNDETERMINED
