import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from evaluation.dataset import EvaluationCase, HttpRequest, load_evaluation_cases


def base_case(**overrides: object) -> dict[str, object]:
    case: dict[str, object] = {
        "case_id": "inventory-zero",
        "case_kind": "bug",
        "bug_id": "INVENTORY_VALIDATION_001",
        "user_report": "Setting inventory to zero is rejected.",
        "endpoint": "/inventory/{product_id}",
        "category": "validation_mismatch",
        "expected_root_cause": "Runtime validation is stricter than the API schema.",
        "expected_status": 200,
        "actual_status": 422,
        "required_evidence": ["The API schema permits zero."],
        "acceptable_tools": ["inspect_api_spec", "execute_api_request"],
        "forbidden_or_unnecessary_tools": [],
        "expected_final_status": "RESOLVED",
        "fixture_id": "sandbox-v1",
        "paraphrase_group": "inventory-zero",
        "setup_requests": [],
        "reproduction_request": {
            "method": "PATCH",
            "path": "/inventory/1",
            "body": {"quantity": 0},
        },
    }
    case.update(overrides)
    return case


def write_cases(tmp_path: Path, cases: list[dict[str, object]]) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases), encoding="utf-8")
    return path


def test_valid_bug_case_loads(tmp_path: Path) -> None:
    cases = load_evaluation_cases(write_cases(tmp_path, [base_case()]))

    assert len(cases) == 1
    assert isinstance(cases[0], EvaluationCase)
    assert cases[0].case_kind == "bug"
    assert cases[0].reproduction_request.body == {"quantity": 0}


def test_valid_no_bug_case_loads(tmp_path: Path) -> None:
    case = base_case(
        case_id="product-present",
        case_kind="no_bug",
        bug_id=None,
        category=None,
        expected_root_cause=None,
        expected_status=200,
        actual_status=200,
        expected_final_status="NO_BUG_FOUND",
        reproduction_request={"method": "GET", "path": "/products/1"},
    )

    loaded = load_evaluation_cases(write_cases(tmp_path, [case]))[0]

    assert loaded.case_kind == "no_bug"
    assert loaded.expected_final_status == "NO_BUG_FOUND"


def test_valid_inconclusive_case_can_omit_reproduction() -> None:
    case = base_case(
        case_id="insufficient-report",
        case_kind="inconclusive",
        bug_id=None,
        endpoint=None,
        category=None,
        expected_root_cause=None,
        expected_status=None,
        actual_status=None,
        expected_final_status="INCONCLUSIVE",
        reproduction_request=None,
    )

    assert EvaluationCase.model_validate(case).reproduction_request is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bug_id", None),
        ("category", None),
        ("endpoint", None),
        ("expected_root_cause", None),
        ("reproduction_request", None),
        ("expected_final_status", "INCONCLUSIVE"),
    ],
)
def test_bug_case_required_fields_and_status_are_enforced(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(base_case(**{field: value}))


@pytest.mark.parametrize("case_kind", ["no_bug", "inconclusive"])
@pytest.mark.parametrize(
    "claimed_ground_truth",
    [
        {"bug_id": "BUG_001"},
        {"category": "business_logic"},
        {"expected_root_cause": "A bug exists."},
    ],
)
def test_non_bug_cases_cannot_claim_bug_ground_truth(
    case_kind: str, claimed_ground_truth: dict[str, str]
) -> None:
    case = base_case(
        case_kind=case_kind,
        bug_id=None,
        category=None,
        expected_root_cause=None,
        expected_final_status=(
            "NO_BUG_FOUND" if case_kind == "no_bug" else "INCONCLUSIVE"
        ),
    )
    case.update(claimed_ground_truth)

    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(case)


@pytest.mark.parametrize(
    "override",
    [
        {"acceptable_tools": ["shell"]},
        {"reproduction_request": {"method": "DELETE", "path": "/products/1"}},
        {"category": "unknown_bug"},
        {"expected_final_status": "LIMIT_REACHED"},
    ],
)
def test_unsupported_contract_values_are_rejected(
    override: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(base_case(**override))


def test_acceptable_and_forbidden_tools_cannot_overlap() -> None:
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(
            base_case(forbidden_or_unnecessary_tools=["execute_api_request"])
        )


def test_malformed_and_extra_fields_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(json.JSONDecodeError):
        (tmp_path / "bad.json").write_text("[{", encoding="utf-8")
        load_evaluation_cases(tmp_path / "bad.json")

    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(base_case(unexpected=True))
    with pytest.raises(ValidationError):
        HttpRequest(method="GET", path="/products/1", timeout=1)


def test_status_pair_cannot_be_partially_specified() -> None:
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(base_case(actual_status=None))


def test_duplicate_case_ids_are_rejected(tmp_path: Path) -> None:
    path = write_cases(tmp_path, [base_case(), base_case()])

    with pytest.raises(ValueError, match="duplicate case_id: inventory-zero"):
        load_evaluation_cases(path)


def test_request_bodies_support_json_compatible_values() -> None:
    request = HttpRequest(
        method="POST",
        path="/orders",
        body={
            "string": "value",
            "integer": 1,
            "number": 1.5,
            "boolean": True,
            "null": None,
            "array": [1, "two", False],
            "object": {"nested": "value"},
        },
    )

    assert request.model_dump_json()
    with pytest.raises(ValidationError):
        HttpRequest(
            method="POST",
            path="/orders",
            body={"timestamp": datetime.now(UTC)},
        )
