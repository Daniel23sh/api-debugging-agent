from uuid import uuid4

import pytest

from agent_service.agent.schemas import (
    DebugDiagnosis,
    DiagnosisStatus,
    FinalAnswerDecision,
    Observation,
    ToolCallDecision,
)
from evaluation.contracts import (
    ArgumentsLabel,
    DecisionRecord,
    EvaluationRun,
    RecordedObservation,
    ResultState,
    SelectionLabel,
)
from evaluation.dataset import load_evaluation_cases
from evaluation.tool_evaluators import (
    evaluate_tool_arguments,
    evaluate_tool_selection,
)

CASES = {
    item.case_id: item
    for item in load_evaluation_cases("evaluation/datasets/v1_cases.json")
}


def decision_run(case, tool, arguments, *, observations=(), available=None):
    recorded = [
        RecordedObservation(observation_id=f"o{i}", observation=observation)
        for i, observation in enumerate(observations)
    ]
    decision = DecisionRecord(
        decision_id="d1",
        prior_observation_ids=(
            [item.observation_id for item in recorded]
            if available is None
            else available
        ),
        decision=ToolCallDecision(
            kind="tool_call", tool_name=tool, arguments=arguments
        ),
        span_id="decision-span",
    )
    return EvaluationRun(
        case_id=case.case_id,
        session_id="session",
        trace_id="trace",
        decisions=[decision],
        observations=recorded,
    ), decision


@pytest.mark.parametrize("tool", ["inspect_api_spec", "execute_api_request"])
def test_alternate_first_actions_do_not_require_sequence(tool):
    case = CASES["inventory_validation_zero_01"]
    arguments = {"method": "PATCH", "path": "/inventory/1"}
    if tool == "execute_api_request":
        arguments["body"] = {"quantity": 0}
    run, decision = decision_run(case, tool, arguments)
    selected = evaluate_tool_selection(case, run, decision)
    assert selected.label == SelectionLabel.APPROPRIATE
    assert selected.decision_id == decision.decision_id
    assert selected.span_id == "decision-span"
    assert selected.trace_id == "trace"
    assert evaluate_tool_arguments(case, run, decision).label == ArgumentsLabel.CORRECT


def test_business_invalid_reproduction_is_correct_arguments():
    case = CASES["inventory_validation_zero_01"]
    run, decision = decision_run(
        case,
        "execute_api_request",
        {"method": "patch", "path": "/inventory/1", "body": {"quantity": 0}},
    )
    assert evaluate_tool_arguments(case, run, decision).label == ArgumentsLabel.CORRECT


@pytest.mark.parametrize(
    "arguments",
    [
        {"method": "GET", "path": "/products/.."},
        {"method": "GET", "path": "/products/%2e%2e"},
        {"method": "GET", "path": "/products/{product_id}"},
        {"method": "DELETE", "path": "/orders"},
        {"method": "GET", "path": "/products/1", "headers": {"x": "secret"}},
    ],
)
def test_bad_arguments_do_not_turn_registered_tool_into_wrong_selection(arguments):
    case = CASES["product_missing_status_01"]
    run, decision = decision_run(case, "execute_api_request", arguments)
    assert (
        evaluate_tool_selection(case, run, decision).label == SelectionLabel.APPROPRIATE
    )
    assert (
        evaluate_tool_arguments(case, run, decision).label == ArgumentsLabel.INCORRECT
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"method": "GET", "path": "/products/not-an-integer"},
        {"method": "GET", "path": "/products/1"},
        {"method": "POST", "path": "/orders", "body": {"product_id": 2, "quantity": 1}},
    ],
)
def test_legal_exploration_is_not_marked_wrong(arguments):
    case = CASES["order_total_quantity_01"]
    run, decision = decision_run(case, "execute_api_request", arguments)
    result = evaluate_tool_arguments(case, run, decision)
    assert result.state == ResultState.UNDETERMINED
    assert result.label is None


def test_unknown_tool_is_selection_error_only():
    case = CASES["inventory_validation_zero_01"]
    run, decision = decision_run(case, "shell", {"command": "pwd"})
    assert (
        evaluate_tool_selection(case, run, decision).label
        == SelectionLabel.INVALID_TOOL
    )
    assert (
        evaluate_tool_arguments(case, run, decision).state == ResultState.NOT_APPLICABLE
    )


def test_acceptable_membership_does_not_prove_contextual_appropriateness():
    case = CASES["order_missing_inventory_01"]
    run, decision = decision_run(
        case, "inspect_server_logs", {"request_id": str(uuid4())}
    )
    assert "inspect_server_logs" in case.acceptable_tools
    assert (
        evaluate_tool_selection(case, run, decision).state == ResultState.UNDETERMINED
    )
    assert (
        evaluate_tool_arguments(case, run, decision).label == ArgumentsLabel.INCORRECT
    )


def test_implementation_first_is_left_to_semantic_review_not_forbidden():
    case = CASES["inventory_validation_zero_01"]
    run, decision = decision_run(
        case,
        "inspect_endpoint_implementation",
        {"method": "PATCH", "path": "/inventory/1"},
    )
    assert (
        evaluate_tool_selection(case, run, decision).state == ResultState.UNDETERMINED
    )
    assert evaluate_tool_arguments(case, run, decision).label == ArgumentsLabel.CORRECT


def test_logs_chain_only_to_prior_execution_not_future_observations():
    case = CASES["order_missing_inventory_01"]
    request_id = str(uuid4())
    execution = Observation(
        tool_name="execute_api_request",
        success=True,
        data={"method": "POST", "path": "/orders", "status_code": 500},
        request_id=request_id,
    )
    run, decision = decision_run(
        case,
        "inspect_server_logs",
        {"request_id": request_id},
        observations=[execution],
    )
    assert (
        evaluate_tool_selection(case, run, decision).label == SelectionLabel.APPROPRIATE
    )
    assert evaluate_tool_arguments(case, run, decision).label == ArgumentsLabel.CORRECT
    decision.prior_observation_ids.clear()
    assert (
        evaluate_tool_selection(case, run, decision).state == ResultState.UNDETERMINED
    )
    assert (
        evaluate_tool_arguments(case, run, decision).label == ArgumentsLabel.INCORRECT
    )


def test_request_id_in_report_is_supported_without_prior_execution():
    request_id = str(uuid4())
    case = CASES["order_missing_inventory_01"].model_copy(
        update={"user_report": f"POST /orders returned 500; X-Request-ID: {request_id}"}
    )
    run, decision = decision_run(
        case, "inspect_server_logs", {"request_id": request_id}
    )
    assert (
        evaluate_tool_selection(case, run, decision).label == SelectionLabel.APPROPRIATE
    )
    assert evaluate_tool_arguments(case, run, decision).label == ArgumentsLabel.CORRECT


def test_guidance_does_not_override_new_runtime_evidence():
    case = CASES["inventory_validation_zero_01"]
    request_id = str(uuid4())
    run, decision = decision_run(
        case,
        "inspect_server_logs",
        {"request_id": request_id},
        observations=[
            Observation(
                tool_name="execute_api_request",
                success=True,
                data={"status_code": 500},
                request_id=request_id,
            )
        ],
    )
    assert "inspect_server_logs" in case.forbidden_or_unnecessary_tools
    assert (
        evaluate_tool_selection(case, run, decision).label == SelectionLabel.APPROPRIATE
    )


def test_same_stable_inspection_can_be_unnecessary_but_http_repetition_is_ambiguous():
    case = CASES["inventory_validation_zero_01"]
    for tool in ("inspect_api_spec", "execute_api_request"):
        run, decision = decision_run(
            case,
            tool,
            {"method": "PATCH", "path": "/inventory/1"},
            observations=[
                Observation(
                    tool_name=tool,
                    success=True,
                    data={"method": "PATCH", "path": "/inventory/{product_id}"},
                )
            ],
        )
        result = evaluate_tool_selection(case, run, decision)
        if tool == "inspect_api_spec":
            assert result.label == SelectionLabel.UNNECESSARY
        else:
            assert result.state == ResultState.UNDETERMINED


def test_timeout_retry_not_unnecessary_selection():
    case = CASES["product_missing_status_01"]
    run, decision = decision_run(
        case,
        "execute_api_request",
        {"method": "GET", "path": "/products/999"},
        observations=[
            Observation(
                tool_name="execute_api_request",
                success=False,
                error={"type": "TOOL_TIMEOUT", "message": "timeout"},
            )
        ],
    )
    assert (
        evaluate_tool_selection(case, run, decision).label == SelectionLabel.APPROPRIATE
    )


def test_setup_request_and_template_inspection_are_correct():
    case = CASES["order_response_schema_02"]
    setup = case.setup_requests[0]
    run, decision = decision_run(
        case, "execute_api_request", setup.model_dump(mode="json")
    )
    assert evaluate_tool_arguments(case, run, decision).label == ArgumentsLabel.CORRECT
    run, decision = decision_run(
        case, "inspect_api_spec", {"method": "GET", "path": "/orders/{order_id}"}
    )
    assert evaluate_tool_arguments(case, run, decision).label == ArgumentsLabel.CORRECT


def test_inconclusive_no_operation_does_not_invent_tool_requirement():
    case = CASES["insufficient_report_no_endpoint"]
    run, decision = decision_run(
        case, "inspect_api_spec", {"method": "GET", "path": "/products/1"}
    )
    assert (
        evaluate_tool_selection(case, run, decision).label == SelectionLabel.UNNECESSARY
    )


@pytest.mark.parametrize(
    "evaluator", [evaluate_tool_selection, evaluate_tool_arguments]
)
def test_mismatched_case_and_unknown_decision_are_rejected(evaluator):
    case = CASES["inventory_validation_zero_01"]
    run, decision = decision_run(
        case, "inspect_api_spec", {"method": "PATCH", "path": "/inventory/1"}
    )
    with pytest.raises(ValueError, match="identifiers differ"):
        evaluator(CASES["order_missing_inventory_01"], run, decision)
    with pytest.raises(ValueError, match="not part"):
        evaluator(case, run, decision.model_copy(update={"decision_id": "unknown"}))


@pytest.mark.parametrize(
    "evaluator", [evaluate_tool_selection, evaluate_tool_arguments]
)
def test_interrupted_decision_is_not_a_tool_failure(evaluator):
    case = CASES["inventory_validation_zero_01"]
    decision = DecisionRecord(decision_id="d1")
    run = EvaluationRun(
        case_id=case.case_id, session_id="session", decisions=[decision]
    )
    assert evaluator(case, run, decision).state == ResultState.UNDETERMINED


@pytest.mark.parametrize(
    "evaluator", [evaluate_tool_selection, evaluate_tool_arguments]
)
def test_final_answer_decision_is_not_applicable(evaluator):
    case = CASES["insufficient_report_no_endpoint"]
    decision = DecisionRecord(
        decision_id="d1",
        decision=FinalAnswerDecision(
            kind="final_answer",
            diagnosis=DebugDiagnosis(
                status=DiagnosisStatus.INCONCLUSIVE,
                missing_evidence=["Operation and request ID"],
            ),
        ),
    )
    run = EvaluationRun(
        case_id=case.case_id, session_id="session", decisions=[decision]
    )
    assert evaluator(case, run, decision).state == ResultState.NOT_APPLICABLE
