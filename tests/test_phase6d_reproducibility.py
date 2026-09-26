from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from evaluation.dataset import (
    EvaluationCase,
    HttpRequest,
    load_evaluation_cases,
)
from sandbox_api.db import Database
from sandbox_api.ground_truth import BUG_GROUND_TRUTH
from sandbox_api.main import REQUEST_ID_HEADER, create_app

DATASET_PATH = (
    Path(__file__).parents[1] / "evaluation" / "datasets" / "v1_cases.json"
)
CASES = load_evaluation_cases(DATASET_PATH)
EXECUTABLE_CASES = [case for case in CASES if case.reproduction_request is not None]
GROUND_TRUTH_BY_ID = {
    ground_truth.bug_id: ground_truth for ground_truth in BUG_GROUND_TRUTH
}


@pytest.fixture
def sandbox(tmp_path: Path) -> Iterator[tuple[TestClient, Database]]:
    database_path = tmp_path / "sandbox.db"
    database = Database(database_path)
    with TestClient(create_app(database_path)) as client:
        yield client, database


def _execute(client: TestClient, request: HttpRequest) -> httpx.Response:
    kwargs = {"json": request.body} if request.body is not None else {}
    return client.request(request.method.value, request.path, **kwargs)


def _assert_reset_fixture(database: Database) -> None:
    assert database.get_product(1).name == "Mechanical Keyboard"
    assert database.get_inventory(1).quantity == 10
    assert database.get_inventory(2).quantity == 5
    assert database.get_inventory(3).quantity == 0
    assert database.get_order(1) is None


def _reset_and_reproduce(
    case: EvaluationCase,
    client: TestClient,
    database: Database,
) -> dict[str, object]:
    database.reset()
    _assert_reset_fixture(database)

    setup_responses = []
    for request in case.setup_requests:
        response = _execute(client, request)
        assert response.status_code < 400
        setup_responses.append(response)

    assert case.reproduction_request is not None
    assert case.actual_status is not None
    response = _execute(client, case.reproduction_request)
    assert response.status_code == case.actual_status
    return _assert_behavior(case, response, setup_responses, database)


def _assert_behavior(
    case: EvaluationCase,
    response: httpx.Response,
    setup_responses: list[httpx.Response],
    database: Database,
) -> dict[str, object]:
    normalized: dict[str, object] = {
        "status_code": response.status_code,
        "body": response.json(),
        "setup": [
            {"status_code": setup.status_code, "body": setup.json()}
            for setup in setup_responses
        ],
    }

    if case.bug_id == "INVENTORY_VALIDATION_001":
        assert response.status_code == 422
        assert database.get_inventory(1).quantity == 10
        normalized["inventory_1"] = 10
    elif case.bug_id == "PRODUCT_STATUS_001":
        assert response.status_code == 200
        assert response.json() == {}
    elif case.bug_id == "ORDER_TOTAL_001":
        assert response.status_code == 201
        assert Decimal(response.json()["total"]) == Decimal("129.50")
        assert database.get_inventory(2).quantity == 3
        normalized["inventory_2"] = 3
    elif case.bug_id == "ORDER_INVENTORY_NULL_001":
        assert response.status_code == 500
        assert response.json() == {"detail": "Internal Server Error"}
        request_id = response.headers[REQUEST_ID_HEADER]
        assert str(UUID(request_id)) == request_id
        logs = database.get_request_logs(request_id)
        assert [log.event_type for log in logs] == [
            "request_received",
            "request_failed",
        ]
        failure = logs[-1]
        assert failure.status_code == 500
        assert failure.error_type == "TypeError"
        assert failure.error_message is not None
        assert "NoneType" in failure.error_message
        normalized["failure"] = {
            "event_type": failure.event_type,
            "status_code": failure.status_code,
            "error_type": failure.error_type,
            "error_message": failure.error_message,
        }
    elif case.bug_id == "ORDER_RESPONSE_SCHEMA_001":
        assert len(setup_responses) == 1
        assert setup_responses[0].status_code == 201
        assert setup_responses[0].json()["id"] == 1
        assert database.get_order(1).id == 1
        assert response.status_code == 200
        assert response.json()["productId"] == 1
        assert "product_id" not in response.json()
        normalized["order_id"] = 1
    elif case.case_id == "order_create_status_no_bug":
        body = response.json()
        assert response.status_code == case.expected_status == 201
        assert body["id"] == 1
        assert body["product_id"] == 1
        assert body["quantity"] == 1
        assert body["status"] == "created"
        assert Decimal(body["total"]) == Decimal("79.99")
        assert database.get_order(1).id == 1
        assert database.get_inventory(1).quantity == 9
        normalized["inventory_1"] = 9
    elif case.case_id == "inventory_update_status_no_bug":
        assert response.status_code == case.expected_status == 200
        assert response.json() == {"product_id": 1, "quantity": 1}
        assert database.get_inventory(1).quantity == 1
        normalized["inventory_1"] = 1
    else:
        raise AssertionError(f"Unhandled executable case {case.case_id}")

    return normalized


def test_dataset_has_seventeen_executable_cases_and_one_inconclusive_case() -> None:
    non_executable = [case for case in CASES if case.reproduction_request is None]

    assert len(CASES) == 18
    assert len(EXECUTABLE_CASES) == 17
    assert len(non_executable) == 1
    case = non_executable[0]
    assert case.case_id == "insufficient_report_no_endpoint"
    assert case.case_kind == "inconclusive"
    assert case.endpoint is None
    assert case.expected_status is None
    assert case.actual_status is None
    assert case.setup_requests == []
    assert case.reproduction_request is None


def test_bug_cases_keep_canonical_ground_truth_requests_and_statuses() -> None:
    bug_cases = [case for case in EXECUTABLE_CASES if case.case_kind == "bug"]

    assert len(bug_cases) == 15
    for case in bug_cases:
        ground_truth = GROUND_TRUTH_BY_ID[case.bug_id]
        assert case.reproduction_request == HttpRequest(
            method=ground_truth.method,
            path=ground_truth.request_path,
            body=ground_truth.request_body,
        )
        assert case.actual_status == ground_truth.actual_status


@pytest.mark.parametrize(
    "case",
    EXECUTABLE_CASES,
    ids=[case.case_id for case in EXECUTABLE_CASES],
)
def test_reset_setup_and_reproduction_are_deterministic(
    case: EvaluationCase,
    sandbox: tuple[TestClient, Database],
) -> None:
    client, database = sandbox

    first = _reset_and_reproduce(case, client, database)
    second = _reset_and_reproduce(case, client, database)

    assert first == second
