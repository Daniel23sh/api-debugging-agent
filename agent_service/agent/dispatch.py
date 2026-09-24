import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, cast

import httpx
from openinference.semconv.trace import (
    OpenInferenceSpanKindValues,
    SpanAttributes,
    ToolCallAttributes,
)
from opentelemetry.semconv.attributes.error_attributes import ERROR_TYPE
from opentelemetry.trace import Span, Status, StatusCode, Tracer
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from agent_service.agent.schemas import (
    JsonObject,
    Observation,
    ToolCallDecision,
    ToolCallRecord,
)
from agent_service.observability import get_tracer
from agent_service.tools.contracts import ToolResult
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

MAX_VALIDATION_ERRORS = 10
MAX_VALIDATION_LOCATION_CHARS = 100
MAX_VALIDATION_MESSAGE_CHARS = 500
MAX_TRACE_VALUE_CHARS = 256


class DispatchErrorType(StrEnum):
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_ARGUMENT_VALIDATION_ERROR = "TOOL_ARGUMENT_VALIDATION_ERROR"


class ToolDispatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool
    tool_call: ToolCallRecord | None = None
    observation: Observation

    @model_validator(mode="after")
    def validate_acceptance(self) -> "ToolDispatchResult":
        if self.accepted != (self.tool_call is not None):
            raise ValueError("accepted dispatches require exactly one tool-call record")
        if not self.accepted and self.observation.success:
            raise ValueError("rejected dispatches require a failed observation")
        return self


ToolExecutor = Callable[
    [BaseModel, httpx.AsyncBaseTransport | None, Database | None],
    Awaitable[ToolResult[Any]],
]


@dataclass(frozen=True, slots=True)
class _RegisteredTool:
    name: str
    args_model: type[BaseModel]
    executor: ToolExecutor


async def _inspect_api_spec(
    args: BaseModel,
    transport: httpx.AsyncBaseTransport | None,
    _: Database | None,
) -> ToolResult[Any]:
    return await inspect_api_spec(cast(InspectApiSpecArgs, args), transport=transport)


async def _execute_api_request(
    args: BaseModel,
    transport: httpx.AsyncBaseTransport | None,
    _: Database | None,
) -> ToolResult[Any]:
    return await execute_api_request(
        cast(ExecuteApiRequestArgs, args),
        transport=transport,
    )


async def _inspect_server_logs(
    args: BaseModel,
    _: httpx.AsyncBaseTransport | None,
    database: Database | None,
) -> ToolResult[Any]:
    return await asyncio.to_thread(
        inspect_server_logs, cast(InspectServerLogsArgs, args), database=database
    )


async def _inspect_endpoint_implementation(
    args: BaseModel,
    _: httpx.AsyncBaseTransport | None,
    __: Database | None,
) -> ToolResult[Any]:
    return await asyncio.to_thread(
        inspect_endpoint_implementation,
        cast(InspectEndpointImplementationArgs, args),
    )


TOOL_REGISTRY: Mapping[str, _RegisteredTool] = MappingProxyType(
    {
        "inspect_api_spec": _RegisteredTool(
            name="inspect_api_spec",
            args_model=InspectApiSpecArgs,
            executor=_inspect_api_spec,
        ),
        "execute_api_request": _RegisteredTool(
            name="execute_api_request",
            args_model=ExecuteApiRequestArgs,
            executor=_execute_api_request,
        ),
        "inspect_server_logs": _RegisteredTool(
            name="inspect_server_logs",
            args_model=InspectServerLogsArgs,
            executor=_inspect_server_logs,
        ),
        "inspect_endpoint_implementation": _RegisteredTool(
            name="inspect_endpoint_implementation",
            args_model=InspectEndpointImplementationArgs,
            executor=_inspect_endpoint_implementation,
        ),
    }
)


def registered_tool_schemas() -> dict[str, JsonObject]:
    return {
        name: registration.args_model.model_json_schema()
        for name, registration in TOOL_REGISTRY.items()
    }


def canonical_call_identity(tool_call: ToolCallRecord) -> str:
    return json.dumps(
        {"tool_name": tool_call.tool_name, "arguments": tool_call.arguments},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _validate_tool_call(
    decision: ToolCallDecision | ToolCallRecord,
) -> tuple[_RegisteredTool, BaseModel] | ToolDispatchResult:
    registration = TOOL_REGISTRY.get(decision.tool_name)
    if registration is None:
        return _rejected(
            decision.tool_name,
            DispatchErrorType.TOOL_NOT_FOUND,
            f"Tool {decision.tool_name!r} is not registered",
        )

    try:
        args = registration.args_model.model_validate(decision.arguments)
    except ValidationError as error:
        return _rejected(
            decision.tool_name,
            DispatchErrorType.TOOL_ARGUMENT_VALIDATION_ERROR,
            "Tool arguments failed validation",
            details=_validation_details(error),
        )

    return registration, args


def prepare_tool_call(
    decision: ToolCallDecision,
) -> ToolCallRecord | ToolDispatchResult:
    """Validate without executing; return a normalized record or rejection feedback."""
    validated = _validate_tool_call(decision)
    if isinstance(validated, ToolDispatchResult):
        return validated
    registration, args = validated
    return ToolCallRecord(
        tool_name=registration.name,
        arguments=args.model_dump(mode="json"),
    )


async def dispatch_tool_call(
    decision: ToolCallDecision | ToolCallRecord,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    database: Database | None = None,
    tracer: Tracer | None = None,
    session_id: str | None = None,
) -> ToolDispatchResult:
    """Execute a request or prepared record, revalidating mutable records at entry."""
    validated = _validate_tool_call(decision)
    if isinstance(validated, ToolDispatchResult):
        return validated
    registration, args = validated
    tool_call = ToolCallRecord(
        tool_name=registration.name,
        arguments=args.model_dump(mode="json"),
    )
    attributes: dict[str, str | bool | int] = {
        SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.TOOL.value,
        SpanAttributes.TOOL_NAME: registration.name,
    }
    if session_id is not None:
        attributes[SpanAttributes.SESSION_ID] = session_id[:MAX_TRACE_VALUE_CHARS]
    safe_arguments = {
        key: value[:MAX_TRACE_VALUE_CHARS]
        for key in ("method", "path", "request_id")
        if isinstance((value := tool_call.arguments.get(key)), str)
    }
    if safe_arguments:
        attributes[ToolCallAttributes.TOOL_CALL_FUNCTION_ARGUMENTS_JSON] = json.dumps(
            safe_arguments, sort_keys=True, separators=(",", ":")
        )

    tracer = tracer if tracer is not None else get_tracer(None)
    with tracer.start_as_current_span(
        registration.name,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as tool_span:
        try:
            tool_result = await registration.executor(args, transport, database)
        except asyncio.CancelledError:
            _mark_tool_error(tool_span, "SESSION_TIMEOUT", "Tool execution interrupted")
            raise
        except Exception as error:
            _mark_tool_error(
                tool_span, type(error).__name__, "Tool executor raised an exception"
            )
            raise
        _record_tool_result(tool_span, tool_result)
    return ToolDispatchResult(
        accepted=True,
        tool_call=tool_call,
        observation=_to_observation(tool_result),
    )


def _mark_tool_error(span: Span, error_type: str, description: str) -> None:
    span.set_attribute(ERROR_TYPE, error_type[:MAX_TRACE_VALUE_CHARS])
    span.set_status(Status(StatusCode.ERROR, description[:MAX_TRACE_VALUE_CHARS]))


def _record_tool_result(span: Span, result: ToolResult[Any]) -> None:
    span.set_attribute("apilens.tool.success", result.success)
    if not result.success:
        _mark_tool_error(
            span,
            result.error_type.value,
            "Tool execution returned a typed failure",
        )
        return

    data = result.data
    if data is None:
        return
    for field in ("method", "path", "request_id"):
        value = getattr(data, field, None)
        if value is not None:
            span.set_attribute(
                f"apilens.tool.{field}", str(value)[:MAX_TRACE_VALUE_CHARS]
            )

    if result.tool_name == "inspect_api_spec":
        span.set_attribute("apilens.tool.contract_found", True)
    elif result.tool_name == "execute_api_request":
        span.set_attribute("apilens.tool.status_code", data.status_code)
    elif result.tool_name == "inspect_server_logs":
        span.set_attribute("apilens.tool.logs_found", True)
        span.set_attribute("apilens.tool.event_count", len(data.events))
    elif result.tool_name == "inspect_endpoint_implementation":
        span.set_attribute("apilens.tool.implementation_found", True)
        span.set_attribute("apilens.tool.section_count", len(data.sections))


def _rejected(
    tool_name: str,
    error_type: DispatchErrorType,
    message: str,
    *,
    details: list[JsonObject] | None = None,
) -> ToolDispatchResult:
    error: JsonObject = {"type": error_type.value, "message": message}
    if details is not None:
        error["details"] = details
    return ToolDispatchResult(
        accepted=False,
        observation=Observation(tool_name=tool_name, success=False, error=error),
    )


def _validation_details(error: ValidationError) -> list[JsonObject]:
    return [
        {
            "location": [
                (
                    part[:MAX_VALIDATION_LOCATION_CHARS]
                    if isinstance(part, str)
                    else part
                    if isinstance(part, int)
                    else str(part)[:MAX_VALIDATION_LOCATION_CHARS]
                )
                for part in item["loc"]
            ],
            "type": item["type"],
            "message": item["msg"][:MAX_VALIDATION_MESSAGE_CHARS],
        }
        for item in error.errors(include_url=False)[:MAX_VALIDATION_ERRORS]
    ]


def _to_observation(result: ToolResult[Any]) -> Observation:
    if result.success:
        data = result.data.model_dump(mode="json")
        request_id = data.get("request_id")
        return Observation(
            tool_name=result.tool_name,
            success=True,
            data=data,
            request_id=request_id if isinstance(request_id, str) else None,
        )

    return Observation(
        tool_name=result.tool_name,
        success=False,
        error={
            "type": result.error_type.value,
            "message": result.error_message,
        },
    )
