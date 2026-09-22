from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sandbox_api.db import Database
from sandbox_api.ground_truth import BUG_GROUND_TRUTH
from sandbox_api.main import REQUEST_ID_HEADER, create_app


@pytest.fixture
def sandbox(tmp_path: Path) -> Iterator[tuple[TestClient, Database]]:
    database_path = tmp_path / "sandbox.db"
    database = Database(database_path)
    with TestClient(create_app(database_path)) as client:
        yield client, database


def test_ground_truth_has_exactly_one_record_per_bug_family() -> None:
    assert len(BUG_GROUND_TRUTH) == 5
    assert len({bug.bug_id for bug in BUG_GROUND_TRUTH}) == 5
    assert {bug.category for bug in BUG_GROUND_TRUTH} == {
        "validation_mismatch",
        "wrong_status_or_response",
        "business_logic",
        "runtime_exception",
        "field_schema_mismatch",
    }


def test_validation_mismatch_is_documented_but_rejected(
    sandbox: tuple[TestClient, Database],
) -> None:
    client, database = sandbox
    openapi = client.get("/openapi.json").json()
    quantity_schema = openapi["components"]["schemas"]["InventoryUpdate"][
        "properties"
    ]["quantity"]

    response = client.patch("/inventory/1", json={"quantity": 0})

    assert quantity_schema["minimum"] == 0
    assert "exclusiveMinimum" not in quantity_schema
    assert response.status_code == 422
    assert database.get_inventory(1).quantity == 10


def test_missing_product_has_wrong_status_and_response(
    sandbox: tuple[TestClient, Database],
) -> None:
    client, _ = sandbox

    response = client.get("/products/999")

    assert response.status_code == 200
    assert response.json() == {}


def test_product_two_order_has_wrong_total(
    sandbox: tuple[TestClient, Database],
) -> None:
    client, database = sandbox

    response = client.post("/orders", json={"product_id": 2, "quantity": 2})

    assert response.status_code == 201
    assert Decimal(response.json()["total"]) == Decimal("129.50")
    assert database.get_inventory(2).quantity == 3


def test_missing_inventory_produces_correlated_http_500(
    sandbox: tuple[TestClient, Database],
) -> None:
    client, database = sandbox

    response = client.post("/orders", json={"product_id": 4, "quantity": 1})

    request_id = response.headers[REQUEST_ID_HEADER]
    logs = database.get_request_logs(request_id)
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal Server Error"}
    assert [log.event_type for log in logs] == [
        "request_received",
        "request_failed",
    ]
    assert logs[-1].status_code == 500
    assert logs[-1].error_type == "TypeError"
    assert "NoneType" in logs[-1].error_message


def test_order_response_uses_wrong_field_name(
    sandbox: tuple[TestClient, Database],
) -> None:
    client, _ = sandbox
    order = client.post(
        "/orders",
        json={"product_id": 1, "quantity": 1},
    ).json()
    openapi = client.get("/openapi.json").json()
    required_fields = openapi["components"]["schemas"]["Order"]["required"]

    response = client.get(f"/orders/{order['id']}")

    assert response.status_code == 200
    assert "product_id" in required_fields
    assert response.json()["productId"] == 1
    assert "product_id" not in response.json()


def test_reset_reproduces_all_seeded_scenarios(
    sandbox: tuple[TestClient, Database],
) -> None:
    client, database = sandbox

    def reproduce() -> list[tuple[int, dict[str, object]]]:
        validation = client.patch("/inventory/1", json={"quantity": 0})
        status = client.get("/products/999")
        business = client.post(
            "/orders",
            json={"product_id": 2, "quantity": 2},
        )
        runtime = client.post(
            "/orders",
            json={"product_id": 4, "quantity": 1},
        )
        order = client.post(
            "/orders",
            json={"product_id": 1, "quantity": 1},
        ).json()
        schema = client.get(f"/orders/{order['id']}")
        return [
            (validation.status_code, validation.json()),
            (status.status_code, status.json()),
            (business.status_code, business.json()),
            (runtime.status_code, runtime.json()),
            (schema.status_code, schema.json()),
        ]

    first_run = reproduce()
    database.reset()
    second_run = reproduce()

    assert first_run == second_run
