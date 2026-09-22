import os
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from agent_service.tools.contracts import HttpMethod, ToolErrorType, ToolResult
from agent_service.tools.endpoints import normalize_approved_endpoint
from sandbox_api.main import REQUEST_ID_HEADER

SANDBOX_BASE_URL = os.getenv("SANDBOX_BASE_URL", "http://127.0.0.1:8000")
SANDBOX_TIMEOUT_SECONDS = 5.0
MAX_RESPONSE_BODY_CHARS = 4096


class ExecuteApiRequestArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: HttpMethod
    path: str = Field(min_length=1)
    body: dict[str, JsonValue] | None = None

    @field_validator("method", mode="before")
    @classmethod
    def normalize_method(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("path", mode="before")
    @classmethod
    def strip_path(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ExecuteApiRequestData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: HttpMethod
    path: str
    status_code: int
    response_body: JsonValue
    response_body_truncated: bool
    request_id: str | None = None


class ExecuteApiRequestResult(ToolResult[ExecuteApiRequestData]):
    tool_name: Literal["execute_api_request"] = "execute_api_request"


async def execute_api_request(
    args: ExecuteApiRequestArgs,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ExecuteApiRequestResult:
    route_template = normalize_approved_endpoint(args.method, args.path)
    if route_template is None or "{" in args.path or "}" in args.path:
        return _failure(
            ToolErrorType.ENDPOINT_NOT_ALLOWED,
            f"{args.method.value} {args.path} is not an executable Sandbox endpoint",
        )

    request_kwargs = {"json": args.body} if args.body is not None else {}
    try:
        async with httpx.AsyncClient(
            base_url=SANDBOX_BASE_URL,
            timeout=SANDBOX_TIMEOUT_SECONDS,
            transport=transport,
        ) as client:
            response = await client.request(
                args.method.value,
                args.path,
                **request_kwargs,
            )
    except httpx.TimeoutException:
        return _failure(
            ToolErrorType.TOOL_TIMEOUT,
            "Timed out while executing the Sandbox API request",
        )
    except httpx.HTTPError as error:
        return _failure(
            ToolErrorType.TOOL_HTTP_ERROR,
            f"Could not execute the Sandbox API request: {error}",
        )

    response_body, truncated = _safe_response_body(response)
    return ExecuteApiRequestResult(
        success=True,
        data=ExecuteApiRequestData(
            method=args.method,
            path=args.path,
            status_code=response.status_code,
            response_body=response_body,
            response_body_truncated=truncated,
            request_id=response.headers.get(REQUEST_ID_HEADER),
        ),
    )


def _safe_response_body(
    response: httpx.Response,
) -> tuple[JsonValue, bool]:
    text = response.text
    if len(text) > MAX_RESPONSE_BODY_CHARS:
        return text[:MAX_RESPONSE_BODY_CHARS], True
    try:
        return response.json(), False
    except ValueError:
        return text, False


def _failure(error_type: ToolErrorType, message: str) -> ExecuteApiRequestResult:
    return ExecuteApiRequestResult(
        success=False,
        error_type=error_type,
        error_message=message,
    )
