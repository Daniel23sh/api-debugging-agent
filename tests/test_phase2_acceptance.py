from pathlib import Path

import httpx
import pytest

from agent_service.tools.execute_api_request import (
    ExecuteApiRequestArgs,
    execute_api_request,
)
from agent_service.tools.inspect_api_spec import InspectApiSpecArgs, inspect_api_spec
from agent_service.tools.inspect_endpoint_implementation import (
    InspectEndpointImplementationArgs,
    inspect_endpoint_implementation,
)
from agent_service.tools.inspect_server_logs import (
    InspectServerLogsArgs,
    inspect_server_logs,
)
from sandbox_api.db import Database
from sandbox_api.main import create_app
from sandbox_api.source_map import SourceSection


@pytest.mark.asyncio
async def test_all_phase_2_tools_compose_without_an_llm(tmp_path: Path) -> None:
    database_path = tmp_path / "sandbox.db"
    database = Database(database_path)
    database.initialize()
    sandbox_app = create_app(database_path)

    spec = await inspect_api_spec(
        InspectApiSpecArgs(method="POST", path="/orders"),
        transport=httpx.ASGITransport(app=sandbox_app),
    )
    execution = await execute_api_request(
        ExecuteApiRequestArgs(
            method="POST",
            path="/orders",
            body={"product_id": 4, "quantity": 1},
        ),
        transport=httpx.ASGITransport(app=sandbox_app),
    )
    assert execution.data is not None
    assert execution.data.request_id is not None
    logs = inspect_server_logs(
        InspectServerLogsArgs(request_id=execution.data.request_id),
        database=database,
    )
    implementation = inspect_endpoint_implementation(
        InspectEndpointImplementationArgs(method="POST", path="/orders")
    )

    assert spec.success is True
    assert spec.data is not None
    assert spec.data.request_required_fields == ["product_id", "quantity"]
    assert execution.success is True
    assert execution.data.status_code == 500
    assert logs.success is True
    assert logs.data is not None
    assert logs.data.events[-1].error_type == "TypeError"
    assert implementation.success is True
    assert implementation.data is not None
    source_by_section = {
        section.section: section.source for section in implementation.data.sections
    }
    assert (
        'inventory["quantity"]'
        in source_by_section[SourceSection.CREATE_ORDER_DATABASE]
    )
