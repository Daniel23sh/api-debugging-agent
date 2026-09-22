from typing import Literal

from pydantic import BaseModel


class BugGroundTruth(BaseModel):
    bug_id: str
    category: Literal[
        "validation_mismatch",
        "wrong_status_or_response",
        "business_logic",
        "runtime_exception",
        "field_schema_mismatch",
    ]
    method: Literal["GET", "POST", "PATCH"]
    endpoint: str
    request_path: str
    request_body: dict[str, object] | None = None
    setup: tuple[str, ...] = ()
    expected_status: int
    actual_status: int
    expected_behavior: str
    actual_behavior: str
    root_cause: str
    required_evidence: tuple[str, ...]


BUG_GROUND_TRUTH = (
    BugGroundTruth(
        bug_id="INVENTORY_VALIDATION_001",
        category="validation_mismatch",
        method="PATCH",
        endpoint="/inventory/{product_id}",
        request_path="/inventory/1",
        request_body={"quantity": 0},
        expected_status=200,
        actual_status=422,
        expected_behavior="Set product 1 inventory to the documented minimum of zero.",
        actual_behavior="Runtime validation rejects quantity zero.",
        root_cause=(
            "InventoryUpdate enforces quantity > 0 while its OpenAPI schema documents "
            "quantity >= 0."
        ),
        required_evidence=(
            "OpenAPI quantity schema has minimum 0",
            "PATCH /inventory/1 with quantity 0 returns 422",
            "runtime model uses a strict greater-than-zero constraint",
        ),
    ),
    BugGroundTruth(
        bug_id="PRODUCT_STATUS_001",
        category="wrong_status_or_response",
        method="GET",
        endpoint="/products/{product_id}",
        request_path="/products/999",
        expected_status=404,
        actual_status=200,
        expected_behavior="Return 404 because product 999 does not exist.",
        actual_behavior="Returns HTTP 200 with an empty object.",
        root_cause="The product-999 not-found branch returns an empty 200 response.",
        required_evidence=(
            "product 999 is absent from deterministic fixtures",
            "GET /products/999 returns 200",
            "response body is an empty object",
        ),
    ),
    BugGroundTruth(
        bug_id="ORDER_TOTAL_001",
        category="business_logic",
        method="POST",
        endpoint="/orders",
        request_path="/orders",
        request_body={"product_id": 2, "quantity": 2},
        expected_status=201,
        actual_status=201,
        expected_behavior="Create an order with total 259.00 and reduce inventory to 3.",
        actual_behavior="Creates the order with total 129.50 while reducing inventory to 3.",
        root_cause="Product 2 order totals use the unit price without multiplying by quantity.",
        required_evidence=(
            "product 2 unit price is 129.50",
            "requested quantity is 2",
            "created order total is 129.50",
            "inventory is reduced by 2",
        ),
    ),
    BugGroundTruth(
        bug_id="ORDER_INVENTORY_NULL_001",
        category="runtime_exception",
        method="POST",
        endpoint="/orders",
        request_path="/orders",
        request_body={"product_id": 4, "quantity": 1},
        expected_status=404,
        actual_status=500,
        expected_behavior="Return 404 because product 4 has no inventory record.",
        actual_behavior="Returns HTTP 500 with a correlated TypeError failure log.",
        root_cause="Order creation subscripts a missing inventory row without a null check.",
        required_evidence=(
            "product 4 exists but has no inventory row",
            "POST /orders returns 500 and X-Request-ID",
            "correlated request_failed log contains a NoneType TypeError",
        ),
    ),
    BugGroundTruth(
        bug_id="ORDER_RESPONSE_SCHEMA_001",
        category="field_schema_mismatch",
        method="GET",
        endpoint="/orders/{order_id}",
        request_path="/orders/1",
        setup=("Create order 1 with POST /orders for product 1 and quantity 1.",),
        expected_status=200,
        actual_status=200,
        expected_behavior="Return the documented product_id response field.",
        actual_behavior="Returns productId instead of product_id.",
        root_cause="The order response bypasses its Pydantic response model and renames product_id.",
        required_evidence=(
            "OpenAPI Order schema requires product_id",
            "GET /orders/1 response contains productId",
            "GET /orders/1 response omits product_id",
        ),
    ),
)
