import asyncio
import json

import pytest
from pydantic import ValidationError

from agent_service.agent.schemas import (
    DebugDiagnosis,
    FinalAnswerDecision,
    ToolCallDecision,
)
from evaluation.calibration import (
    CALIBRATION_CASE_IDS,
    HumanReview,
    calibration_cases,
    collect_calibration,
    compare_labels,
    finalize_review,
    load_calibration_runs,
)


@pytest.mark.asyncio
async def test_six_serial_runs_reset_setup_and_blind_review(tmp_path):
    active = 0
    max_active = 0

    class Provider:
        async def next_decision(self, state):
            nonlocal active, max_active
            active += 1
            max_active = max(active, max_active)
            try:
                await asyncio.sleep(0)
                if not state.observations:
                    return ToolCallDecision(
                        kind="tool_call",
                        tool_name="execute_api_request",
                        arguments={"method": "GET", "path": "/orders/1"},
                    )
                return FinalAnswerDecision(
                    kind="final_answer",
                    diagnosis=DebugDiagnosis(
                        status="INCONCLUSIVE",
                        missing_evidence=["mock calibration only"],
                    ),
                )
            finally:
                active -= 1

    directory = tmp_path / "calibration"
    runs = await collect_calibration(directory, Provider, model="mock-agent")
    assert [run.case_id for run in runs] == list(CALIBRATION_CASE_IDS)
    assert len({run.session_id for run in runs}) == 6 and max_active == 1
    # Only schema case has an order created by the canonical setup.
    statuses = [run.observations[0].observation.data["status_code"] for run in runs]
    assert statuses == [404, 404, 404, 200, 404, 404]
    packet = json.loads((directory / "human-review.json").read_text())
    assert len(packet) == 6 and "judge" not in json.dumps(packet)
    assert all(
        item["grounding"] is None
        for item in json.loads((directory / "human-labels.json").read_text())
    )
    assert load_calibration_runs(directory) == runs
    with pytest.raises(FileExistsError):
        await collect_calibration(directory, Provider, model="mock-agent")


def results():
    return {
        case_id: {
            "root_cause": {
                "state": "SCORED" if index < 4 else "NOT_APPLICABLE",
                "label": "CORRECT" if index < 4 else None,
            },
            "grounding": {"state": "SCORED", "label": "GROUNDED"},
            "response_quality": {"state": "SCORED", "label": "GOOD"},
        }
        for index, case_id in enumerate(CALIBRATION_CASE_IDS)
    }


def labels(directory, *, missing=False, disagreement=False):
    records = [
        {
            "case_id": case_id,
            "root_cause": "CORRECT" if index < 4 else "NOT_APPLICABLE",
            "grounding": "GROUNDED",
            "response_quality": "GOOD",
        }
        for index, case_id in enumerate(CALIBRATION_CASE_IDS)
    ]
    if missing:
        records[-1]["grounding"] = None
    if disagreement:
        records[0]["response_quality"] = "POOR"
    (directory / "human-labels.json").write_text(json.dumps(records))


def test_cannot_reveal_comparison_before_all_human_labels(tmp_path):
    labels(tmp_path, missing=True)
    with pytest.raises(ValidationError):
        compare_labels(tmp_path, results())


def test_human_review_is_required_and_disagreements_need_disposition(tmp_path):
    labels(tmp_path, disagreement=True)
    comparison = compare_labels(tmp_path, results())
    assert comparison["calibration_complete"] is False
    assert sum(item["agreement"] is False for item in comparison["comparisons"]) == 1
    with pytest.raises(ValueError):
        finalize_review(comparison, HumanReview())
    review = HumanReview(
        resolutions=[
            {
                "case_id": CALIBRATION_CASE_IDS[0],
                "evaluator": "response_quality",
                "category": "rubric",
                "resolution": "Clarified usefulness rubric and rechecked affected saved examples",
            }
        ],
        no_material_unresolved_pattern=True,
        comment="Human review complete",
    )
    assert finalize_review(comparison, review)["calibration_complete"]
    unavailable = results()
    unavailable[CALIBRATION_CASE_IDS[0]]["response_quality"] = {
        "state": "ERROR",
        "label": None,
    }
    with pytest.raises(ValueError, match="evaluated again"):
        finalize_review(compare_labels(tmp_path, unavailable), review)


def test_fixed_calibration_subset_preserves_phase6():
    cases = calibration_cases()
    assert len(cases) == 6
    assert sum(case.case_kind == "bug" for case in cases) == 4
    assert sum(case.case_kind == "no_bug" for case in cases) == 1
    assert sum(case.case_kind == "inconclusive" for case in cases) == 1


@pytest.fixture
def reviewed_disagreements(tmp_path):
    labels(tmp_path, disagreement=True)
    judged = results()
    judged[CALIBRATION_CASE_IDS[0]]["grounding"]["label"] = "UNGROUNDED"
    judged[CALIBRATION_CASE_IDS[0]]["response_quality"]["label"] = "NEEDS_IMPROVEMENT"
    comparison = compare_labels(tmp_path, judged)
    review = HumanReview(
        resolutions=[
            {
                "case_id": CALIBRATION_CASE_IDS[0],
                "evaluator": evaluator,
                "category": "rubric",
                "resolution": rationale,
            }
            for evaluator, rationale in (
                (
                    "grounding",
                    "Recorded application failure supports the claim, independently of task success.",
                ),
                (
                    "response_quality",
                    "An internal-failure-only answer gives no useful explanation or next step.",
                ),
            )
        ],
        no_material_unresolved_pattern=True,
        comment="Human review confirmed no unresolved material pattern",
    )
    return comparison, review


def test_finalization_retains_reviewed_disagreements(reviewed_disagreements):
    comparison, review = reviewed_disagreements
    before = json.dumps(comparison, sort_keys=True)
    finalized = finalize_review(comparison, review)
    assert finalized["calibration_complete"]
    assert sum(item["agreement"] is False for item in finalized["comparisons"]) == 2
    assert finalized["comparisons"] == comparison["comparisons"]
    assert json.dumps(comparison, sort_keys=True) == before


@pytest.mark.parametrize("invalid", ["empty", "partial", "duplicate", "extraneous"])
def test_every_disagreement_requires_exactly_one_resolution(
    reviewed_disagreements, invalid
):
    comparison, review = reviewed_disagreements
    records = review.resolutions
    if invalid == "empty":
        records = []
    elif invalid == "partial":
        records = records[:1]
    elif invalid == "duplicate":
        records = [*records, records[0]]
    else:
        records = [*records, records[0].model_copy(update={"evaluator": "root_cause"})]
    invalid_review = review.model_copy(update={"resolutions": records})
    with pytest.raises(ValueError, match="exactly one resolution"):
        finalize_review(comparison, invalid_review)


@pytest.mark.parametrize(
    "missing", [{"no_material_unresolved_pattern": False}, {"comment": " "}]
)
def test_complete_dispositions_still_require_human_confirmation(
    reviewed_disagreements, missing
):
    comparison, review = reviewed_disagreements
    with pytest.raises(ValueError, match="human confirmation"):
        finalize_review(comparison, review.model_copy(update=missing))


@pytest.mark.parametrize("state", ["ERROR", "UNDETERMINED"])
def test_resolving_an_unavailable_judgment_cannot_close_calibration(
    reviewed_disagreements, state
):
    comparison, review = reviewed_disagreements
    comparison = json.loads(json.dumps(comparison))
    for item in comparison["comparisons"]:
        if (
            item["evaluator"] == "grounding"
            and item["case_id"] == CALIBRATION_CASE_IDS[0]
        ):
            item.update(judge=state, agreement=None)
    with pytest.raises(ValueError, match="evaluated again"):
        finalize_review(comparison, review)
