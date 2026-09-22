import json
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError

from agent_service.tools.contracts import ToolErrorType
from agent_service.tools.execute_api_request import (
    MAX_RESPONSE_BODY_CHARS,
    ExecuteApiRequestArgs,
    execute_api_request,
)
from sandbox_api.db import Database
from sandbox_api.main import REQUEST_ID_HEADER, create_app


def test_execute_api_request_input_allows_only_request_controls() -> None:
    args = ExecuteApiRequestArgs(
        method="post",
        path=" /orders ",
        body={"product_id": 1, "quantity": 2},
    )
    assert args.method.value == "POST"
    assert args.path == "/orders"

    with pytest.raises(ValidationError):
        ExecuteApiRequestArgs(
            method="GET",
            path="/products/1",
            url="https://example.com",
        )
    with pytest.raises(ValidationError):
        ExecuteApiRequestArgs(
            method="POST",
            path="/orders",
            body={"not_json": object()},
        )


@pytest.mark.asyncio
async def test_method_path_and_body_are_forwarded() -> None:
    request_id = str(uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        assert request.url.path == "/inventory/7"
        assert json.loads(request.content) == {"quantity": 4}
        return httpx.Response(
            200,
            json={"product_id": 7, "quantity": 4},
            headers={REQUEST_ID_HEADER: request_id},
        )

    result = await execute_api_request(
        ExecuteApiRequestArgs(
            method="PATCH",
            path="/inventory/7",
            body={"quantity": 4},
        ),
        transport=httpx.MockTransport(handler),
    )

    assert result.success is True
    assert result.data is not None
    assert result.data.method.value == "PATCH"
    assert result.data.path == "/inventory/7"
    assert result.data.status_code == 200
    assert result.data.response_body == {"product_id": 7, "quantity": 4}
    assert result.data.response_body_truncated is False
    assert result.data.request_id == request_id


@pytest.mark.asyncio
async def test_request_without_body_sends_no_json_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.content == b""
        return httpx.Response(200, json={"id": 1})

    result = await execute_api_request(
        ExecuteApiRequestArgs(method="GET", path="/products/1"),
        transport=httpx.MockTransport(handler),
    )

    assert result.success is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/products/1"),
        ("GET", "/admin/debug"),
        ("GET", "/orders/{order_id}"),
        ("GET", "/orders/{other}"),
    ],
)
async def test_non_executable_endpoints_are_rejected_before_http(
    method: str,
    path: str,
) -> None:
    requested = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requested
        requested = True
        return httpx.Response(200)

    result = await execute_api_request(
        ExecuteApiRequestArgs(method=method, path=path),
        transport=httpx.MockTransport(handler),
    )

    assert result.success is False
    assert result.error_type is ToolErrorType.ENDPOINT_NOT_ALLOWED
    assert requested is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "expected_status", "expected_body"),
    [
        (
            "/products/1",
            200,
            {"id": 1, "name": "Mechanical Keyboard", "price": "79.99"},
        ),
        ("/products/998", 404, {"detail": "Product not found"}),
    ],
)
async def test_sandbox_responses_are_successful_tool_results(
    tmp_path: Path,
    path: str,
    expected_status: int,
    expected_body: dict[str, object],
) -> None:
    database_path = tmp_path / "sandbox.db"
    Database(database_path).initialize()

    result = await execute_api_request(
        ExecuteApiRequestArgs(method="GET", path=path),
        transport=httpx.ASGITransport(app=create_app(database_path)),
    )

    assert result.success is True
    assert result.data is not None
    assert result.data.status_code == expected_status
    assert result.data.response_body == expected_body
    assert result.data.request_id is not None
    assert str(UUID(result.data.request_id)) == result.data.request_id


@pytest.mark.asyncio
async def test_seeded_sandbox_500_is_a_successful_tool_result(tmp_path: Path) -> None:
    database_path = tmp_path / "sandbox.db"
    Database(database_path).initialize()

    result = await execute_api_request(
        ExecuteApiRequestArgs(
            method="POST",
            path="/orders",
            body={"product_id": 4, "quantity": 1},
        ),
        transport=httpx.ASGITransport(app=create_app(database_path)),
    )

    assert result.success is True
    assert result.data is not None
    assert result.data.status_code == 500
    assert result.data.response_body == {"detail": "Internal Server Error"}
    assert result.data.request_id is not None
    assert str(UUID(result.data.request_id)) == result.data.request_id


@pytest.mark.asyncio
async def test_sandbox_422_is_a_successful_tool_result(tmp_path: Path) -> None:
    database_path = tmp_path / "sandbox.db"
    Database(database_path).initialize()

    result = await execute_api_request(
        ExecuteApiRequestArgs(
            method="POST",
            path="/orders",
            body={"product_id": 1, "quantity": 0},
        ),
        transport=httpx.ASGITransport(app=create_app(database_path)),
    )

    assert result.success is True
    assert result.data is not None
    assert result.data.status_code == 422
    assert result.data.request_id is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["connection", "timeout"])
async def test_http_client_failures_are_tool_failures(failure: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        raise httpx.ConnectError("unavailable", request=request)

    result = await execute_api_request(
        ExecuteApiRequestArgs(method="GET", path="/products/1"),
        transport=httpx.MockTransport(handler),
    )

    assert result.success is False
    assert result.data is None
    expected = (
        ToolErrorType.TOOL_TIMEOUT
        if failure == "timeout"
        else ToolErrorType.TOOL_HTTP_ERROR
    )
    assert result.error_type is expected


@pytest.mark.asyncio
async def test_response_body_is_bounded() -> None:
    result = await execute_api_request(
        ExecuteApiRequestArgs(method="GET", path="/products/1"),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text="x" * (MAX_RESPONSE_BODY_CHARS + 1))
        ),
    )

    assert result.data is not None
    assert result.data.response_body == "x" * MAX_RESPONSE_BODY_CHARS
    assert result.data.response_body_truncated is True
