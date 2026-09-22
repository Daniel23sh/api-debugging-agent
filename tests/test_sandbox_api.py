from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sandbox_api.db import Database
from sandbox_api.main import create_app


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "sandbox.db"


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
