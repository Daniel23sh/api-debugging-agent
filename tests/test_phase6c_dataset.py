from collections import Counter
from pathlib import Path

from evaluation.dataset import HttpRequest, load_evaluation_cases

DATASET_PATH = (
    Path(__file__).parents[1] / "evaluation" / "datasets" / "v1_cases.json"
)
ALL_TOOLS = {
    "inspect_api_spec",
    "execute_api_request",
    "inspect_server_logs",
    "inspect_endpoint_implementation",
}
NO_BUG_ACCEPTABLE_TOOLS = {"inspect_api_spec", "execute_api_request"}
NO_BUG_UNNECESSARY_TOOLS = {
    "inspect_server_logs",
    "inspect_endpoint_implementation",
}


def test_complete_v1_dataset_has_expected_composition_and_outcomes() -> None:
    cases = load_evaluation_cases(DATASET_PATH)
    expected_status = {
        "bug": "RESOLVED",
        "no_bug": "NO_BUG_FOUND",
        "inconclusive": "INCONCLUSIVE",
    }

    assert len(cases) == 18
    assert Counter(case.case_kind for case in cases) == {
        "bug": 15,
        "no_bug": 2,
        "inconclusive": 1,
    }
    assert all(
        case.expected_final_status == expected_status[case.case_kind]
        for case in cases
    )
    assert len({case.case_id for case in cases}) == len(cases)
    assert len({case.user_report for case in cases}) == len(cases)
    assert {case.fixture_id for case in cases} == {"sandbox-v1"}


def test_no_bug_cases_capture_correct_sandbox_behavior() -> None:
    cases = {
        case.case_id: case
        for case in load_evaluation_cases(DATASET_PATH)
        if case.case_kind == "no_bug"
    }
    expected = {
        "order_create_status_no_bug": (
            "/orders",
            201,
            HttpRequest(
                method="POST",
                path="/orders",
                body={"product_id": 1, "quantity": 1},
            ),
        ),
        "inventory_update_status_no_bug": (
            "/inventory/{product_id}",
            200,
            HttpRequest(
                method="PATCH",
                path="/inventory/1",
                body={"quantity": 1},
            ),
        ),
    }

    assert cases.keys() == expected.keys()
    for case_id, (endpoint, status, reproduction) in expected.items():
        case = cases[case_id]
        assert case.bug_id is None
        assert case.category is None
        assert case.expected_root_cause is None
        assert case.endpoint == endpoint
        assert case.expected_status == case.actual_status == status
        assert case.reproduction_request == reproduction
        assert case.setup_requests == []
        assert case.paraphrase_group is None
        assert case.acceptable_tools == NO_BUG_ACCEPTABLE_TOOLS
        assert case.forbidden_or_unnecessary_tools == NO_BUG_UNNECESSARY_TOOLS


def test_no_bug_cases_have_only_contract_and_runtime_evidence() -> None:
    cases = {
        case.case_id: case
        for case in load_evaluation_cases(DATASET_PATH)
        if case.case_kind == "no_bug"
    }

    assert cases["order_create_status_no_bug"].required_evidence == [
        "the API contract documents HTTP 201 for successful POST /orders",
        "POST /orders with product 1 and quantity 1 returns HTTP 201",
    ]
    assert cases["inventory_update_status_no_bug"].required_evidence == [
        "the API contract documents HTTP 200 for a successful inventory update",
        "PATCH /inventory/1 with quantity 1 returns HTTP 200 and represents quantity 1",
    ]


def test_inconclusive_case_has_no_investigable_request() -> None:
    cases = [
        case
        for case in load_evaluation_cases(DATASET_PATH)
        if case.case_kind == "inconclusive"
    ]

    assert len(cases) == 1
    case = cases[0]
    assert case.case_id == "insufficient_report_no_endpoint"
    assert case.bug_id is None
    assert case.category is None
    assert case.expected_root_cause is None
    assert case.endpoint is None
    assert case.expected_status is None
    assert case.actual_status is None
    assert case.required_evidence == []
    assert case.setup_requests == []
    assert case.reproduction_request is None
    assert case.paraphrase_group is None
    assert case.acceptable_tools == set()
    assert case.forbidden_or_unnecessary_tools == ALL_TOOLS
