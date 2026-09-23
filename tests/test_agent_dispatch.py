import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from agent_service.agent.dispatch import (
    TOOL_REGISTRY,
    DispatchErrorType,
    canonical_call_identity,
    dispatch_tool_call,
    registered_tool_schemas,
)
from agent_service.agent.schemas import ToolCallDecision
from agent_service.agent.state import DebugSessionState
from agent_service.tools.execute_api_request import ExecuteApiRequestArgs
from agent_service.tools.inspect_api_spec import InspectApiSpecArgs
from agent_service.tools.inspect_endpoint_implementation import (
    InspectEndpointImplementationArgs,
)
from agent_service.tools.inspect_server_logs import InspectServerLogsArgs
from sandbox_api.db import Database
from sandbox_api.main import create_app


def decision(tool_name: str, arguments: dict) -> ToolCallDecision:
    return ToolCallDecision(
        kind="tool_call",
        tool_name=tool_name,
        arguments=arguments,
    )


def test_registry_is_closed_to_the_four_approved_args_models() -> None:
    assert {
        name: registration.args_model for name, registration in TOOL_REGISTRY.items()
    } == {
        "inspect_api_spec": InspectApiSpecArgs,
        "execute_api_request": ExecuteApiRequestArgs,
        "inspect_server_logs": InspectServerLogsArgs,
        "inspect_endpoint_implementation": InspectEndpointImplementationArgs,
    }


def test_registry_exposes_provider_neutral_json_schemas() -> None:
    schemas = registered_tool_schemas()
    assert set(schemas) == set(TOOL_REGISTRY)
    assert schemas["inspect_api_spec"]["properties"]["method"]
    assert schemas["inspect_server_logs"]["properties"]["request_id"]
    json.dumps(schemas)


@pytest.mark.asyncio
async def test_unknown_tool_is_rejected_without_execution() -> None:
    requested = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requested
        requested = True
        return httpx.Response(200)

    result = await dispatch_tool_call(
        decision("delete_everything", {}),
        transport=httpx.MockTransport(handler),
    )

    assert result.accepted is False
    assert result.tool_call is None
    assert result.observation.success is False
    assert result.observation.error["type"] == DispatchErrorType.TOOL_NOT_FOUND
    assert requested is False


@pytest.mark.asyncio
async def test_invalid_arguments_return_json_safe_feedback_without_execution() -> None:
    requested = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requested
        requested = True
        return httpx.Response(200)

    result = await dispatch_tool_call(
        decision(
            "execute_api_request",
            {"method": "INVALID", "path": "/products/1"},
        ),
        transport=httpx.MockTransport(handler),
    )

    assert result.accepted is False
    assert result.tool_call is None
    error = result.observation.error
    assert error["type"] == DispatchErrorType.TOOL_ARGUMENT_VALIDATION_ERROR
    detail = error["details"][0]
    assert detail["location"] == ["method"]
    assert detail["type"]
    assert detail["message"]
    json.dumps(result.model_dump(mode="json"))
    assert requested is False


@pytest.mark.asyncio
async def test_valid_arguments_are_normalized_before_recording() -> None:
    result = await dispatch_tool_call(
        decision(
            "inspect_endpoint_implementation",
            {"method": "post", "path": " /orders "},
        )
    )

    assert result.accepted is True
    assert result.tool_call is not None
    assert result.tool_call.arguments == {"method": "POST", "path": "/orders"}
    assert result.observation.success is True


@pytest.mark.asyncio
async def test_uuid_arguments_are_recorded_as_json_strings(tmp_path: Path) -> None:
    database = Database(tmp_path / "sandbox.db")
    database.initialize()
    request_id = uuid4()
    database.add_request_log(
        request_id=str(request_id),
        event_type="request_received",
        method="GET",
        path="/products/1",
    )

    result = await dispatch_tool_call(
        decision("inspect_server_logs", {"request_id": str(request_id)}),
        database=database,
    )

    assert result.tool_call is not None
    assert result.tool_call.arguments == {"request_id": str(request_id)}
    assert result.observation.request_id == str(request_id)
    assert result.observation.success is True


@pytest.mark.asyncio
async def test_all_four_tools_share_one_async_dispatch_surface(tmp_path: Path) -> None:
    database_path = tmp_path / "sandbox.db"
    database = Database(database_path)
    database.initialize()
    request_id = uuid4()
    database.add_request_log(
        request_id=str(request_id),
        event_type="request_received",
        method="GET",
        path="/products/1",
    )
    transport = httpx.ASGITransport(app=create_app(database_path))
    calls = [
        decision("inspect_api_spec", {"method": "GET", "path": "/products/1"}),
        decision("execute_api_request", {"method": "GET", "path": "/products/1"}),
        decision("inspect_server_logs", {"request_id": str(request_id)}),
        decision(
            "inspect_endpoint_implementation",
            {"method": "GET", "path": "/products/1"},
        ),
    ]

    results = [
        await dispatch_tool_call(call, transport=transport, database=database)
        for call in calls
    ]

    assert all(result.accepted for result in results)
    assert all(result.observation.success for result in results)
    assert [result.observation.tool_name for result in results] == list(TOOL_REGISTRY)


@pytest.mark.asyncio
async def test_phase_two_failure_remains_an_executed_failed_observation() -> None:
    result = await dispatch_tool_call(
        decision(
            "inspect_api_spec",
            {"method": "POST", "path": "/products/1"},
        )
    )

    assert result.accepted is True
    assert result.tool_call is not None
    assert result.observation.success is False
    assert result.observation.error == {
        "type": "ENDPOINT_NOT_ALLOWED",
        "message": "POST /products/1 is not an approved Sandbox endpoint",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        ({"product_id": 1, "quantity": 0}, 422),
        ({"product_id": 4, "quantity": 1}, 500),
    ],
)
async def test_sandbox_error_status_is_successful_observation(
    tmp_path: Path,
    body: dict,
    expected_status: int,
) -> None:
    database_path = tmp_path / "sandbox.db"
    Database(database_path).initialize()

    result = await dispatch_tool_call(
        decision(
            "execute_api_request",
            {"method": "POST", "path": "/orders", "body": body},
        ),
        transport=httpx.ASGITransport(app=create_app(database_path)),
    )

    assert result.accepted is True
    assert result.observation.success is True
    assert result.observation.data["status_code"] == expected_status
    assert result.observation.request_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        (
            "execute_api_request",
            {
                "method": "GET",
                "path": "/products/1",
                "transport": "model-controlled",
            },
        ),
        (
            "inspect_server_logs",
            {"request_id": "dc1c7458-d12c-4c20-9601-f86f6a4466a5", "database": "/tmp/x"},
        ),
    ],
)
async def test_application_dependencies_cannot_be_tool_arguments(
    tool_name: str,
    arguments: dict,
) -> None:
    result = await dispatch_tool_call(decision(tool_name, arguments))
    assert result.accepted is False
    assert result.observation.error["type"] == (
        DispatchErrorType.TOOL_ARGUMENT_VALIDATION_ERROR
    )


@pytest.mark.asyncio
async def test_canonical_identity_uses_validated_normalized_arguments() -> None:
    first = await dispatch_tool_call(
        decision(
            "inspect_endpoint_implementation",
            {"path": " /orders ", "method": "post"},
        )
    )
    equivalent = await dispatch_tool_call(
        decision(
            "inspect_endpoint_implementation",
            {"method": "POST", "path": "/orders"},
        )
    )
    different = await dispatch_tool_call(
        decision(
            "inspect_endpoint_implementation",
            {"method": "GET", "path": "/orders/1"},
        )
    )

    assert first.tool_call is not None
    assert equivalent.tool_call is not None
    assert different.tool_call is not None
    assert canonical_call_identity(first.tool_call) == canonical_call_identity(
        equivalent.tool_call
    )
    assert canonical_call_identity(first.tool_call) != canonical_call_identity(
        different.tool_call
    )


@pytest.mark.asyncio
async def test_dispatch_does_not_mutate_session_state() -> None:
    state = DebugSessionState(session_id="session-1", issue="Inspect products")
    before = state.model_dump()

    await dispatch_tool_call(
        decision(
            "inspect_endpoint_implementation",
            {"method": "GET", "path": "/products/1"},
        )
    )

    assert state.model_dump() == before
