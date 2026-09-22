"""Narrow, application-controlled Sandbox investigation tools."""

from agent_service.tools.contracts import HttpMethod, ToolErrorType, ToolResult
from agent_service.tools.inspect_api_spec import (
    InspectApiSpecArgs,
    InspectApiSpecData,
    InspectApiSpecResult,
    inspect_api_spec,
)

__all__ = [
    "HttpMethod",
    "InspectApiSpecArgs",
    "InspectApiSpecData",
    "InspectApiSpecResult",
    "ToolErrorType",
    "ToolResult",
    "inspect_api_spec",
]
