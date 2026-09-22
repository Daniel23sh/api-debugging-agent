import os
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_service.tools.contracts import HttpMethod, ToolErrorType, ToolResult
from agent_service.tools.endpoints import normalize_approved_endpoint

SANDBOX_BASE_URL = os.getenv("SANDBOX_BASE_URL", "http://127.0.0.1:8000")
SANDBOX_TIMEOUT_SECONDS = 5.0
SCHEMA_REF_PREFIX = "#/components/schemas/"


class InspectApiSpecArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: HttpMethod
    path: str = Field(min_length=1)

    @field_validator("method", mode="before")
    @classmethod
    def normalize_method(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("path", mode="before")
    @classmethod
    def strip_path(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ApiResponseContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    response_schema: dict[str, Any] | None = None


class InspectApiSpecData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: HttpMethod
    path: str
    operation_id: str | None = None
    summary: str | None = None
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    request_body_required: bool = False
    request_schema: dict[str, Any] | None = None
    request_required_fields: list[str] = Field(default_factory=list)
    documented_status_codes: list[str]
    responses: dict[str, ApiResponseContract]
    schemas: dict[str, dict[str, Any]]


class InspectApiSpecResult(ToolResult[InspectApiSpecData]):
    tool_name: Literal["inspect_api_spec"] = "inspect_api_spec"


async def inspect_api_spec(
    args: InspectApiSpecArgs,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> InspectApiSpecResult:
    route_template = normalize_approved_endpoint(args.method, args.path)
    if route_template is None:
        return _failure(
            ToolErrorType.ENDPOINT_NOT_ALLOWED,
            f"{args.method.value} {args.path} is not an approved Sandbox endpoint",
        )

    try:
        async with httpx.AsyncClient(
            base_url=SANDBOX_BASE_URL,
            timeout=SANDBOX_TIMEOUT_SECONDS,
            transport=transport,
        ) as client:
            response = await client.get("/openapi.json")
            response.raise_for_status()
            document = response.json()
        data = _build_contract(document, args.method, route_template)
    except httpx.TimeoutException:
        return _failure(
            ToolErrorType.TOOL_TIMEOUT,
            "Timed out while retrieving the Sandbox OpenAPI contract",
        )
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        return _failure(
            ToolErrorType.TOOL_HTTP_ERROR,
            f"Could not retrieve a valid Sandbox OpenAPI contract: {error}",
        )

    return InspectApiSpecResult(success=True, data=data)


def _build_contract(
    document: dict[str, Any],
    method: HttpMethod,
    route_template: str,
) -> InspectApiSpecData:
    operation = document["paths"][route_template][method.value.lower()]
    all_schemas = document.get("components", {}).get("schemas", {})
    request_body = operation.get("requestBody", {})
    request_schema = (
        request_body.get("content", {}).get("application/json", {}).get("schema")
    )
    relevant_schemas = _referenced_schemas(operation, all_schemas)
    responses = {
        status_code: ApiResponseContract(
            description=response.get("description"),
            response_schema=response.get("content", {})
            .get("application/json", {})
            .get("schema"),
        )
        for status_code, response in operation.get("responses", {}).items()
    }

    return InspectApiSpecData(
        method=method,
        path=route_template,
        operation_id=operation.get("operationId"),
        summary=operation.get("summary"),
        parameters=operation.get("parameters", []),
        request_body_required=request_body.get("required", False),
        request_schema=request_schema,
        request_required_fields=_required_fields(request_schema, all_schemas),
        documented_status_codes=list(responses),
        responses=responses,
        schemas=relevant_schemas,
    )


def _referenced_schemas(
    operation: dict[str, Any],
    all_schemas: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    names = _schema_references(operation)
    relevant: dict[str, dict[str, Any]] = {}
    while names:
        name = names.pop()
        if name in relevant:
            continue
        schema = all_schemas[name]
        relevant[name] = schema
        names.update(_schema_references(schema))
    return dict(sorted(relevant.items()))


def _schema_references(value: object) -> set[str]:
    if isinstance(value, dict):
        references = {
            ref.removeprefix(SCHEMA_REF_PREFIX)
            for ref in [value.get("$ref")]
            if isinstance(ref, str) and ref.startswith(SCHEMA_REF_PREFIX)
        }
        for nested in value.values():
            references.update(_schema_references(nested))
        return references
    if isinstance(value, list):
        return set().union(*map(_schema_references, value)) if value else set()
    return set()


def _required_fields(
    schema: dict[str, Any] | None,
    all_schemas: dict[str, dict[str, Any]],
) -> list[str]:
    if schema is None:
        return []
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith(SCHEMA_REF_PREFIX):
        schema = all_schemas[ref.removeprefix(SCHEMA_REF_PREFIX)]
    return schema.get("required", [])


def _failure(error_type: ToolErrorType, message: str) -> InspectApiSpecResult:
    return InspectApiSpecResult(
        success=False,
        error_type=error_type,
        error_message=message,
    )
