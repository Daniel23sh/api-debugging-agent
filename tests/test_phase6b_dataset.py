from collections import Counter, defaultdict
from pathlib import Path

from evaluation.dataset import HttpRequest, load_evaluation_cases
from sandbox_api.ground_truth import BUG_GROUND_TRUTH, BugGroundTruth

DATASET_PATH = (
    Path(__file__).parents[1] / "evaluation" / "datasets" / "v1_cases.json"
)
EXPECTED_GROUPS = {
    "INVENTORY_VALIDATION_001": "inventory_validation_zero",
    "PRODUCT_STATUS_001": "product_missing_status",
    "ORDER_TOTAL_001": "order_total_quantity",
    "ORDER_INVENTORY_NULL_001": "order_missing_inventory",
    "ORDER_RESPONSE_SCHEMA_001": "order_response_schema",
}
STANDARD_TOOLS = {
    "inspect_api_spec",
    "execute_api_request",
    "inspect_endpoint_implementation",
}
ALL_TOOLS = STANDARD_TOOLS | {"inspect_server_logs"}


def _cases_by_bug() -> dict[str, list]:
    grouped = defaultdict(list)
    for case in load_evaluation_cases(DATASET_PATH):
        grouped[case.bug_id].append(case)
    return grouped


def _expected_reproduction(ground_truth: BugGroundTruth) -> HttpRequest:
    return HttpRequest(
        method=ground_truth.method,
        path=ground_truth.request_path,
        body=ground_truth.request_body,
    )


def test_v1_positive_dataset_has_five_groups_of_three_bug_cases() -> None:
    cases = load_evaluation_cases(DATASET_PATH)
    counts = Counter(case.bug_id for case in cases)

    assert len(cases) == 15
    assert all(case.case_kind == "bug" for case in cases)
    assert all(case.expected_final_status == "RESOLVED" for case in cases)
    assert counts == {bug_id: 3 for bug_id in EXPECTED_GROUPS}
    assert {case.bug_id for case in cases} == {
        ground_truth.bug_id for ground_truth in BUG_GROUND_TRUTH
    }
    assert len({case.case_id for case in cases}) == len(cases)
    assert {case.fixture_id for case in cases} == {"sandbox-v1"}


def test_case_ids_groups_and_reports_are_distinct_and_stable() -> None:
    cases_by_bug = _cases_by_bug()
    all_reports = []

    for bug_id, group in EXPECTED_GROUPS.items():
        cases = cases_by_bug[bug_id]
        reports = [case.user_report for case in cases]
        assert {case.paraphrase_group for case in cases} == {group}
        assert {case.case_id for case in cases} == {
            f"{group}_{number:02d}" for number in range(1, 4)
        }
        assert len(set(reports)) == 3
        assert all(report.strip() for report in reports)
        all_reports.extend(reports)

    assert len(set(all_reports)) == len(all_reports)


def test_every_case_matches_authoritative_seeded_ground_truth() -> None:
    cases_by_bug = _cases_by_bug()

    for ground_truth in BUG_GROUND_TRUTH:
        cases = cases_by_bug[ground_truth.bug_id]
        expected_reproduction = _expected_reproduction(ground_truth)
        for case in cases:
            assert case.category == ground_truth.category
            assert case.endpoint == ground_truth.endpoint
            assert case.expected_root_cause == ground_truth.root_cause
            assert case.expected_status == ground_truth.expected_status
            assert case.actual_status == ground_truth.actual_status
            assert case.required_evidence == list(ground_truth.required_evidence)
            assert case.reproduction_request == expected_reproduction
            assert ground_truth.root_cause.lower() not in case.user_report.lower()


def test_each_paraphrase_group_shares_one_ground_truth_record() -> None:
    for cases in _cases_by_bug().values():
        canonical = {
            (
                case.category,
                case.endpoint,
                case.expected_root_cause,
                case.expected_status,
                case.actual_status,
                tuple(case.required_evidence),
                case.reproduction_request.model_dump_json(),
            )
            for case in cases
        }
        assert len(canonical) == 1


def test_only_order_response_schema_cases_have_structured_setup() -> None:
    expected_setup = [
        HttpRequest(
            method="POST",
            path="/orders",
            body={"product_id": 1, "quantity": 1},
        )
    ]

    for case in load_evaluation_cases(DATASET_PATH):
        if case.bug_id == "ORDER_RESPONSE_SCHEMA_001":
            assert case.setup_requests == expected_setup
        else:
            assert case.setup_requests == []


def test_tool_ground_truth_allows_alternate_trajectories() -> None:
    for case in load_evaluation_cases(DATASET_PATH):
        if case.bug_id == "ORDER_INVENTORY_NULL_001":
            assert case.acceptable_tools == ALL_TOOLS
            assert case.forbidden_or_unnecessary_tools == set()
        else:
            assert case.acceptable_tools == STANDARD_TOOLS
            assert case.forbidden_or_unnecessary_tools == {"inspect_server_logs"}
