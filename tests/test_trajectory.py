import pytest

from agent_service.agent.schemas import DebugDiagnosis, ToolCallRecord
from evaluation.contracts import (
    ComparisonContext,
    DecisionRecord,
    EvaluationRun,
    EvaluatorResult,
    ToolAttempt,
)
from evaluation.dataset import load_evaluation_cases
from evaluation.trajectory import assess_success, convergence, evaluate_trajectory


def case():
    return load_evaluation_cases("evaluation/datasets/v1_cases.json")[0]


def run(session="s", *, decisions=2, attempts=1, success=True, retry=False):
    selected = case()
    return EvaluationRun(
        case_id=selected.case_id,
        session_id=session,
        diagnosis=DebugDiagnosis(
            status="RESOLVED", affected_endpoint=selected.endpoint,
            root_cause="cause", suggested_fix="fix",
            evidence=[{"source": "api_execution", "finding": "observed"}],
        ) if success else DebugDiagnosis(
            status="INCONCLUSIVE", missing_evidence=["missing"]
        ),
        decisions=[DecisionRecord(decision_id=f"d{i}") for i in range(decisions)],
        tool_attempts=[ToolAttempt(
            attempt_id=f"t{i}", decision_id="d0",
            call=ToolCallRecord(
                tool_name="inspect_api_spec", arguments={"method": "GET", "path": "/products/1"}
            ),
            timeout_retry=retry and i > 0,
        ) for i in range(attempts)],
        comparison=ComparisonContext(
            dataset_version="v1", fixture_id=selected.fixture_id,
            agent_version="test", model="test",
        ),
    )


def result(run, name="grounding", label="GROUNDED", state="SCORED", **kwargs):
    return EvaluatorResult(
        evaluator=name, unit="run", case_id=run.case_id, session_id=run.session_id,
        state=state, label=label, explanation="test", **kwargs,
    )


def success_results(run):
    return [result(run), result(run, "root_cause", "CORRECT")]


def test_path_counts_attempts_and_separates_timeout_retries_from_repeated_calls():
    current = run(attempts=3, retry=True)
    selection = EvaluatorResult(
        evaluator="tool_selection", unit="decision", decision_id="d0",
        case_id=current.case_id, session_id=current.session_id,
        state="SCORED", label="UNNECESSARY", explanation="test",
    )
    metrics = evaluate_trajectory(case(), current, [selection]).value
    assert metrics["decision_attempts"] == 2
    assert metrics["tool_attempts"] == 3
    assert metrics["path_length"] == 5
    assert metrics["duplicate_calls"] == 2
    assert metrics["non_retry_duplicate_calls"] == 0
    assert metrics["timeout_retries"] == 2
    assert metrics["unnecessary_tool_attempts"] == 3
    assert [item["kind"] for item in metrics["path"]] == [
        "decision", "tool", "tool", "tool", "decision"
    ]
    current.tool_attempts[-1].timeout_retry = False
    assert evaluate_trajectory(case(), current).value["non_retry_duplicate_calls"] == 1


@pytest.mark.parametrize("state,label,error_type", [
    ("UNDETERMINED", None, None), ("ERROR", None, "JUDGE_API_ERROR"),
    ("SCORED", "PARTIALLY_GROUNDED", None),
])
def test_missing_error_and_partial_grounding_do_not_qualify(state, label, error_type):
    current = run()
    results = [result(current, state=state, label=label, error_type=error_type)]
    assert not assess_success(case(), current, results).eligible


def test_root_cause_and_expected_status_are_required_independently():
    current = run()
    assert not assess_success(case(), current, [result(current)]).eligible
    partial = [result(current), result(current, "root_cause", "PARTIALLY_CORRECT")]
    assert not assess_success(case(), current, partial).eligible
    assert assess_success(case(), current, success_results(current)).eligible
    current.diagnosis = None
    assert not assess_success(case(), current, success_results(current)).eligible


def test_convergence_excludes_short_failed_run_and_ties_alternate_paths():
    failed = run("failed", decisions=1, attempts=0, success=False)
    shortest = run("short")
    alternate = run("alternate")
    alternate.tool_attempts[0].call.arguments["path"] = "/products/2"
    longer = run("long", decisions=3, attempts=2)
    runs = [failed, shortest, alternate, longer]
    results = convergence(
        {case().case_id: case()}, runs,
        {item.session_id: success_results(item) for item in runs},
    )
    assert [item.ratio for item in results] == [None, 1, 1, 3 / 5]
    assert results[0].reason == "FINAL_STATUS_MISMATCH"
    assert results[-1].shortest_successful_observed_path == 3


def test_no_successes_and_single_success_are_explicit():
    current = run(success=False)
    output = convergence({case().case_id: case()}, [current], {})
    assert output[0].ratio is None
    current = run()
    output = convergence(
        {case().case_id: case()}, [current], {current.session_id: success_results(current)}
    )
    assert output[0].ratio == 1  # Observed optimum only, not proof of global optimality.


@pytest.mark.parametrize("change", [
    "model", "model_settings", "dataset", "agent", "fixture", "limits", "versions", "judge_model",
])
def test_incomparable_successful_runs_have_separate_baselines(change):
    shortest = run("short")
    other = run("other", decisions=4, attempts=2)
    if change == "model":
        other.comparison.model = "another-model"
    elif change == "dataset":
        other.comparison.dataset_version = "v2"
    elif change == "agent":
        other.comparison.agent_version = "different-agent"
    elif change == "fixture":
        other.comparison.fixture_id = "different-fixture"
    elif change == "limits":
        other.comparison.limits.max_tool_calls += 1
    elif change == "model_settings":
        other.comparison.model_settings = {"reasoning_effort": "high"}
    results = {item.session_id: success_results(item) for item in [shortest, other]}
    if change == "versions":
        results[other.session_id][0].version = "2"
    elif change == "judge_model":
        results[other.session_id][0].judge_model = "different-judge"
    output = convergence({case().case_id: case()}, [shortest, other], results)
    assert output[0].ratio == 1
    assert output[1].ratio is None if change == "fixture" else output[1].ratio == 1


def test_mismatched_result_identifiers_are_rejected():
    current = run()
    unrelated = result(current)
    unrelated.session_id = "other"
    with pytest.raises(ValueError, match="identifiers"):
        evaluate_trajectory(case(), current, [unrelated])


def test_missing_comparison_and_paraphrase_group_produce_no_ratio():
    selected = case()
    current = run()
    current.comparison = None
    output = convergence(
        {selected.case_id: selected}, [current], {current.session_id: success_results(current)}
    )
    assert output[0].ratio is None
    assert output[0].reason == "MISSING_COMPARISON_CONTEXT"
    selected.paraphrase_group = None
    output = convergence(
        {selected.case_id: selected}, [current], {current.session_id: success_results(current)}
    )
    assert output[0].reason == "NO_PARAPHRASE_GROUP"


def test_non_bug_success_requires_grounding_without_a_root_cause_judgment():
    selected = next(
        item for item in load_evaluation_cases("evaluation/datasets/v1_cases.json")
        if item.case_kind == "inconclusive"
    )
    current = EvaluationRun(
        case_id=selected.case_id, session_id="s",
        diagnosis=DebugDiagnosis(status="INCONCLUSIVE", missing_evidence=["endpoint needed"]),
    )
    assert assess_success(selected, current, [result(current)]).eligible
