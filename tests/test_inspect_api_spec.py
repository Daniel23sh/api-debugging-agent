from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from agent_service.tools.contracts import HttpMethod, ToolErrorType
from agent_service.tools.endpoints import normalize_approved_endpoint
from agent_service.tools.inspect_api_spec import (
    InspectApiSpecArgs,
    InspectApiSpecData,
    InspectApiSpecResult,
    inspect_api_spec,
)
from sandbox_api.db import Database
from sandbox_api.main import create_app


def test_inspect_api_spec_contracts_validate_result_states() -> None:
    args = InspectApiSpecArgs(method="get", path=" /orders/1 ")
    assert args == InspectApiSpecArgs(method=HttpMethod.GET, path="/orders/1")

    with pytest.raises(ValidationError):
        InspectApiSpecArgs(method="DELETE", path="/orders/1")
    with pytest.raises(ValidationError):
        InspectApiSpecArgs(method="GET", path="/orders/1", extra=True)
    with pytest.raises(ValidationError):
        InspectApiSpecResult(success=False)
    with pytest.raises(ValidationError):
        InspectApiSpecResult(
            success=True,
            data=InspectApiSpecData(
                method=HttpMethod.GET,
                path="/orders/{order_id}",
                documented_status_codes=[],
                responses={},
                schemas={},
            ),
            error_type=ToolErrorType.TOOL_HTTP_ERROR,
            error_message="unexpected",
        )


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        (HttpMethod.POST, "/orders", "/orders"),
        (HttpMethod.GET, "/orders/{order_id}", "/orders/{order_id}"),
        (HttpMethod.GET, "/orders/1", "/orders/{order_id}"),
        (HttpMethod.PATCH, "/inventory/42", "/inventory/{product_id}"),
    ],
)
def test_approved_endpoints_are_normalized(
    method: HttpMethod,
    path: str,
    expected: str,
) -> None:
    assert normalize_approved_endpoint(method, path) == expected


@pytest.mark.parametrize(
    ("method", "path"),
    [
        (HttpMethod.POST, "/products/1"),
        (HttpMethod.GET, "/admin/debug"),
        (HttpMethod.GET, "/orders/1/extra"),
        (HttpMethod.GET, "/orders/1?include=payments"),
    ],
)
def test_unsupported_method_path_combinations_are_rejected(
    method: HttpMethod,
    path: str,
) -> None:
    assert normalize_approved_endpoint(method, path) is None


@pytest.mark.asyncio
async def test_inspect_api_spec_returns_only_requested_real_contract(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "sandbox.db"
    Database(database_path).initialize()
    transport = httpx.ASGITransport(app=create_app(database_path))

    result = await inspect_api_spec(
        InspectApiSpecArgs(method="POST", path="/orders"),
        transport=transport,
    )

    assert result.success is True
    assert result.data is not None
    assert result.data.path == "/orders"
    assert result.data.request_body_required is True
    assert result.data.request_schema == {"$ref": "#/components/schemas/OrderCreate"}
    assert result.data.request_required_fields == ["product_id", "quantity"]
    assert result.data.documented_status_codes == ["201", "422"]
    assert result.data.responses["201"].response_schema == {
        "$ref": "#/components/schemas/Order"
    }
    assert "product_id" in result.data.schemas["Order"]["required"]
    assert (
        result.data.schemas["OrderCreate"]["properties"]["quantity"]["exclusiveMinimum"]
        == 0
    )
    assert "Product" not in result.data.schemas
    assert "Payment" not in result.data.schemas
    assert "paths" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_inspect_api_spec_preserves_documented_validation_constraints(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "sandbox.db"
    Database(database_path).initialize()
    transport = httpx.ASGITransport(app=create_app(database_path))

    result = await inspect_api_spec(
        InspectApiSpecArgs(method="PATCH", path="/inventory/1"),
        transport=transport,
    )

    assert result.data is not None
    assert result.data.path == "/inventory/{product_id}"
    quantity = result.data.schemas["InventoryUpdate"]["properties"]["quantity"]
    assert quantity["minimum"] == 0
    assert "exclusiveMinimum" not in quantity


@pytest.mark.asyncio
async def test_invalid_endpoint_returns_failure_without_http_request() -> None:
    requested = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requested
        requested = True
        return httpx.Response(200, json={})

    result = await inspect_api_spec(
        InspectApiSpecArgs(method="POST", path="/products/1"),
        transport=httpx.MockTransport(handler),
    )

    assert result.success is False
    assert result.error_type is ToolErrorType.ENDPOINT_NOT_ALLOWED
    assert result.data is None
    assert requested is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["connection", "http", "timeout"])
async def test_sandbox_failures_return_structured_tool_errors(failure: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "connection":
            raise httpx.ConnectError("unavailable", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(503, request=request)

    result = await inspect_api_spec(
        InspectApiSpecArgs(method="GET", path="/products/1"),
        transport=httpx.MockTransport(handler),
    )

    assert result.success is False
    assert result.data is None
    assert result.error_message
    expected = (
        ToolErrorType.TOOL_TIMEOUT
        if failure == "timeout"
        else ToolErrorType.TOOL_HTTP_ERROR
    )
    assert result.error_type is expected
