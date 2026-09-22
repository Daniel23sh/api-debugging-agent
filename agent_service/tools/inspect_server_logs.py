import os
import sqlite3
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from agent_service.tools.contracts import ToolErrorType, ToolResult
from sandbox_api.db import Database

SANDBOX_DATABASE_PATH = os.getenv("SANDBOX_DATABASE_PATH", "sandbox.db")


class InspectServerLogsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID


class ServerLogEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str
    method: str
    path: str
    status_code: int | None = None
    error_type: str | None = None
    error_message: str | None = None


class InspectServerLogsData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    events: list[ServerLogEvent] = Field(min_length=1)


class InspectServerLogsResult(ToolResult[InspectServerLogsData]):
    tool_name: Literal["inspect_server_logs"] = "inspect_server_logs"


def inspect_server_logs(
    args: InspectServerLogsArgs,
    *,
    database: Database | None = None,
) -> InspectServerLogsResult:
    database = database or Database(SANDBOX_DATABASE_PATH)
    request_id = str(args.request_id)
    try:
        logs = database.get_request_logs(request_id)
    except sqlite3.Error as error:
        return _failure(
            ToolErrorType.TOOL_DATABASE_ERROR,
            f"Could not inspect Sandbox request logs: {error}",
        )

    if not logs:
        return _failure(
            ToolErrorType.LOG_NOT_FOUND,
            f"No Sandbox logs found for request ID {request_id}",
        )

    return InspectServerLogsResult(
        success=True,
        data=InspectServerLogsData(
            request_id=args.request_id,
            events=[
                ServerLogEvent(
                    event_type=log.event_type,
                    method=log.method,
                    path=log.path,
                    status_code=log.status_code,
                    error_type=log.error_type,
                    error_message=log.error_message,
                )
                for log in logs
            ],
        ),
    )


def _failure(error_type: ToolErrorType, message: str) -> InspectServerLogsResult:
    return InspectServerLogsResult(
        success=False,
        error_type=error_type,
        error_message=message,
    )
