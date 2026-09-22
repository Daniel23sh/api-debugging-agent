from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from sandbox_api.db import Database
from sandbox_api.main import REQUEST_ID_HEADER, create_app


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "sandbox.db"


@pytest.fixture
def database(database_path: Path) -> Database:
    return Database(database_path)


@pytest.fixture
def client(database_path: Path):
    with TestClient(create_app(database_path)) as test_client:
        yield test_client


def test_existing_product_returns_product(client: TestClient) -> None:
    response = client.get("/products/1")

    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "name": "Mechanical Keyboard",
        "price": "79.99",
    }


def test_missing_product_returns_404(client: TestClient) -> None:
    response = client.get("/products/999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Product not found"}


def test_inventory_update_returns_updated_record(client: TestClient) -> None:
    response = client.patch("/inventory/1", json={"quantity": 7})

    assert response.status_code == 200
    assert response.json() == {"product_id": 1, "quantity": 7}


@pytest.mark.parametrize(
    ("product_id", "quantity", "expected_status"),
    [(999, 3, 404), (1, -1, 422)],
)
def test_invalid_inventory_update_fails(
    client: TestClient,
    product_id: int,
    quantity: int,
    expected_status: int,
) -> None:
    response = client.patch(
        f"/inventory/{product_id}",
        json={"quantity": quantity},
    )

    assert response.status_code == expected_status


def test_order_creation_updates_inventory_and_can_be_retrieved(
    client: TestClient,
    database_path: Path,
) -> None:
    response = client.post("/orders", json={"product_id": 1, "quantity": 2})

    assert response.status_code == 201
    order = response.json()
    assert Decimal(order["total"]) == Decimal("159.98")
    assert order["status"] == "created"
    assert Database(database_path).get_inventory(1).quantity == 8

    retrieved = client.get(f"/orders/{order['id']}")
    assert retrieved.status_code == 200
    assert retrieved.json() == order


def test_order_for_unknown_product_returns_404(client: TestClient) -> None:
    response = client.post("/orders", json={"product_id": 999, "quantity": 1})

    assert response.status_code == 404
    assert response.json() == {"detail": "Product not found"}


def test_order_with_insufficient_inventory_returns_409(client: TestClient) -> None:
    response = client.post("/orders", json={"product_id": 3, "quantity": 1})

    assert response.status_code == 409
    assert response.json() == {"detail": "Insufficient inventory"}


def test_order_with_invalid_quantity_returns_422(client: TestClient) -> None:
    response = client.post("/orders", json={"product_id": 1, "quantity": 0})

    assert response.status_code == 422


def test_payment_uses_order_total(client: TestClient) -> None:
    order = client.post(
        "/orders",
        json={"product_id": 2, "quantity": 2},
    ).json()

    response = client.post("/payments", json={"order_id": order["id"]})

    assert response.status_code == 201
    assert response.json() == {
        "id": 1,
        "order_id": order["id"],
        "amount": order["total"],
        "status": "succeeded",
    }


def test_payment_for_missing_order_returns_404(client: TestClient) -> None:
    response = client.post("/payments", json={"order_id": 999})

    assert response.status_code == 404
    assert response.json() == {"detail": "Order not found"}


def test_openapi_describes_v1_routes(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert set(response.json()["paths"]) == {
        "/products/{product_id}",
        "/inventory/{product_id}",
        "/orders",
        "/orders/{order_id}",
        "/payments",
    }


def test_successful_request_has_correlated_logs(
    client: TestClient,
    database: Database,
) -> None:
    response = client.get("/products/1")

    request_id = response.headers[REQUEST_ID_HEADER]
    assert str(UUID(request_id)) == request_id

    logs = database.get_request_logs(request_id)
    assert [log.event_type for log in logs] == [
        "request_received",
        "response_sent",
    ]
    assert all(log.request_id == request_id for log in logs)
    assert all(log.method == "GET" for log in logs)
    assert all(log.path == "/products/1" for log in logs)
    assert logs[-1].status_code == 200
    assert logs[-1].error_type is None


def test_distinct_requests_receive_distinct_ids(client: TestClient) -> None:
    first = client.get("/products/1")
    second = client.get("/products/1")

    assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]


def test_error_response_is_logged(
    client: TestClient,
    database: Database,
) -> None:
    response = client.get("/products/999")

    request_id = response.headers[REQUEST_ID_HEADER]
    logs = database.get_request_logs(request_id)
    assert logs[-1].event_type == "response_sent"
    assert logs[-1].status_code == 404
    assert logs[-1].error_type == "HTTP_ERROR"


def test_unhandled_error_is_logged_with_request_id(
    database_path: Path,
) -> None:
    app = create_app(database_path)

    @app.get("/_test/error", include_in_schema=False)
    def fail() -> None:
        raise RuntimeError("controlled test failure")

    with TestClient(app) as test_client:
        response = test_client.get("/_test/error")

    request_id = response.headers[REQUEST_ID_HEADER]
    logs = Database(database_path).get_request_logs(request_id)
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal Server Error"}
    assert [log.event_type for log in logs] == [
        "request_received",
        "request_failed",
    ]
    assert all(log.request_id == request_id for log in logs)
    assert logs[-1].event_type == "request_failed"
    assert logs[-1].status_code == 500
    assert logs[-1].error_type == "RuntimeError"
    assert logs[-1].error_message == "controlled test failure"


def test_reset_restores_fixtures_and_removes_mutable_records(
    client: TestClient,
    database: Database,
) -> None:
    with database.connect() as connection:
        connection.execute(
            "UPDATE products SET name = ? WHERE id = ?",
            ("Changed", 1),
        )
        connection.execute(
            "INSERT INTO products (id, name, price) VALUES (?, ?, ?)",
            (99, "Extra", "1.00"),
        )
        connection.execute(
            "INSERT INTO inventory (product_id, quantity) VALUES (?, ?)",
            (99, 1),
        )
    client.patch("/inventory/1", json={"quantity": 3})
    order = client.post("/orders", json={"product_id": 2, "quantity": 1}).json()
    client.post("/payments", json={"order_id": order["id"]})

    database.reset()

    assert database.get_product(1).name == "Mechanical Keyboard"
    assert database.get_inventory(1).quantity == 10
    assert database.get_inventory(2).quantity == 5
    assert database.get_product(99) is None
    assert database.get_order(order["id"]) is None
    with database.connect() as connection:
        payment_count = connection.execute(
            "SELECT COUNT(*) FROM payments"
        ).fetchone()[0]
    assert payment_count == 0


def test_reset_clears_request_logs(
    client: TestClient,
    database: Database,
) -> None:
    response = client.get("/products/1")
    request_id = response.headers[REQUEST_ID_HEADER]
    assert database.get_request_logs(request_id)

    database.reset()

    assert database.get_request_logs(request_id) == []


def test_reset_restarts_generated_ids_deterministically(
    client: TestClient,
    database: Database,
) -> None:
    def create_records() -> tuple[int, int]:
        order = client.post(
            "/orders",
            json={"product_id": 1, "quantity": 1},
        ).json()
        payment = client.post(
            "/payments",
            json={"order_id": order["id"]},
        ).json()
        return order["id"], payment["id"]

    first_ids = create_records()
    database.reset()
    second_ids = create_records()
    database.reset()

    assert first_ids == second_ids == (1, 1)
    assert database.get_inventory(1).quantity == 10
