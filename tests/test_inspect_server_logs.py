from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError

from agent_service.tools.contracts import ToolErrorType
from agent_service.tools.execute_api_request import (
    ExecuteApiRequestArgs,
    execute_api_request,
)
from agent_service.tools.inspect_server_logs import (
    InspectServerLogsArgs,
    inspect_server_logs,
)
from sandbox_api.db import Database
from sandbox_api.main import create_app


def test_request_id_contract_rejects_malformed_or_extra_input() -> None:
    with pytest.raises(ValidationError):
        InspectServerLogsArgs(request_id="not-a-uuid")
    with pytest.raises(ValidationError):
        InspectServerLogsArgs(
            request_id=str(uuid4()),
            database_path="/tmp/other.db",
        )


def test_valid_unknown_request_id_returns_log_not_found(tmp_path: Path) -> None:
    database = Database(tmp_path / "sandbox.db")
    database.initialize()

    result = inspect_server_logs(
        InspectServerLogsArgs(request_id=uuid4()),
        database=database,
    )

    assert result.success is False
    assert result.data is None
    assert result.error_type is ToolErrorType.LOG_NOT_FOUND


def test_logs_are_ordered_and_limited_to_requested_id(tmp_path: Path) -> None:
    database = Database(tmp_path / "sandbox.db")
    database.initialize()
    requested_id = str(uuid4())
    unrelated_id = str(uuid4())
    database.add_request_log(
        request_id=requested_id,
        event_type="request_received",
        method="GET",
        path="/products/1",
    )
    database.add_request_log(
        request_id=unrelated_id,
        event_type="request_failed",
        method="POST",
        path="/orders",
        status_code=500,
        error_type="TypeError",
        error_message="must not leak",
    )
    database.add_request_log(
        request_id=requested_id,
        event_type="response_sent",
        method="GET",
        path="/products/1",
        status_code=200,
    )

    result = inspect_server_logs(
        InspectServerLogsArgs(request_id=requested_id),
        database=database,
    )

    assert result.success is True
    assert result.data is not None
    assert result.data.request_id == UUID(requested_id)
    assert [event.event_type for event in result.data.events] == [
        "request_received",
        "response_sent",
    ]
    assert all(event.path == "/products/1" for event in result.data.events)
    assert all(event.error_message != "must not leak" for event in result.data.events)


def test_database_failure_is_structured(tmp_path: Path) -> None:
    result = inspect_server_logs(
        InspectServerLogsArgs(request_id=uuid4()),
        database=Database(tmp_path / "missing" / "sandbox.db"),
    )

    assert result.success is False
    assert result.error_type is ToolErrorType.TOOL_DATABASE_ERROR
    assert result.error_message


@pytest.mark.asyncio
async def test_request_id_chains_execution_to_correlated_logs(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "sandbox.db"
    database = Database(database_path)
    database.initialize()

    execution = await execute_api_request(
        ExecuteApiRequestArgs(
            method="POST",
            path="/orders",
            body={"product_id": 4, "quantity": 1},
        ),
        transport=httpx.ASGITransport(app=create_app(database_path)),
    )
    assert execution.data is not None
    assert execution.data.request_id is not None

    inspection = inspect_server_logs(
        InspectServerLogsArgs(request_id=execution.data.request_id),
        database=database,
    )

    assert inspection.success is True
    assert inspection.data is not None
    assert str(inspection.data.request_id) == execution.data.request_id
    assert [event.event_type for event in inspection.data.events] == [
        "request_received",
        "request_failed",
    ]
    assert inspection.data.events[-1].status_code == 500
    assert inspection.data.events[-1].error_type == "TypeError"
    assert "NoneType" in inspection.data.events[-1].error_message
