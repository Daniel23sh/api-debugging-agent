import inspect
import textwrap
from typing import Final, Literal

from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_service.tools.contracts import HttpMethod, ToolErrorType, ToolResult
from agent_service.tools.endpoints import normalize_approved_endpoint
from sandbox_api.db import Database
from sandbox_api.main import app as sandbox_app
from sandbox_api.models import (
    InventoryUpdate,
    Order,
    OrderCreate,
    PaymentCreate,
    Product,
)
from sandbox_api.source_map import SourceSection, get_source_sections

MAX_SOURCE_CHARS = 4096

TRUSTED_SOURCE_TARGETS: Final[dict[SourceSection, object]] = {
    SourceSection.PRODUCT_MODEL: Product,
    SourceSection.INVENTORY_UPDATE_MODEL: InventoryUpdate,
    SourceSection.ORDER_CREATE_MODEL: OrderCreate,
    SourceSection.ORDER_MODEL: Order,
    SourceSection.PAYMENT_CREATE_MODEL: PaymentCreate,
    SourceSection.GET_PRODUCT_ROUTE: "get_product",
    SourceSection.UPDATE_INVENTORY_ROUTE: "update_inventory",
    SourceSection.CREATE_ORDER_ROUTE: "create_order",
    SourceSection.GET_ORDER_ROUTE: "get_order",
    SourceSection.CREATE_PAYMENT_ROUTE: "create_payment",
    SourceSection.GET_PRODUCT_DATABASE: Database.get_product,
    SourceSection.UPDATE_INVENTORY_DATABASE: Database.update_inventory,
    SourceSection.CREATE_ORDER_DATABASE: Database.create_order,
    SourceSection.GET_ORDER_DATABASE: Database.get_order,
    SourceSection.CREATE_PAYMENT_DATABASE: Database.create_payment,
}


class InspectEndpointImplementationArgs(BaseModel):
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


class ImplementationSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: SourceSection
    source: str = Field(min_length=1, max_length=MAX_SOURCE_CHARS)
    truncated: bool


class InspectEndpointImplementationData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: HttpMethod
    path: str
    sections: list[ImplementationSection] = Field(min_length=1)


class InspectEndpointImplementationResult(
    ToolResult[InspectEndpointImplementationData]
):
    tool_name: Literal["inspect_endpoint_implementation"] = (
        "inspect_endpoint_implementation"
    )


def inspect_endpoint_implementation(
    args: InspectEndpointImplementationArgs,
) -> InspectEndpointImplementationResult:
    route_template = normalize_approved_endpoint(args.method, args.path)
    if route_template is None:
        return _failure(
            ToolErrorType.ENDPOINT_NOT_ALLOWED,
            f"{args.method.value} {args.path} is not an approved Sandbox endpoint",
        )

    source_sections = get_source_sections(args.method.value, route_template)
    try:
        sections = [_inspect_section(section) for section in source_sections or ()]
    except (LookupError, OSError, TypeError, ValueError):
        return _failure(
            ToolErrorType.SOURCE_RESOLUTION_ERROR,
            f"Could not resolve approved source for {args.method.value} {route_template}",
        )

    if not sections:
        return _failure(
            ToolErrorType.SOURCE_RESOLUTION_ERROR,
            f"No approved source is mapped for {args.method.value} {route_template}",
        )

    return InspectEndpointImplementationResult(
        success=True,
        data=InspectEndpointImplementationData(
            method=args.method,
            path=route_template,
            sections=sections,
        ),
    )


def _inspect_section(section: SourceSection) -> ImplementationSection:
    source = textwrap.dedent(inspect.getsource(_resolve_source_object(section))).strip()
    truncated = len(source) > MAX_SOURCE_CHARS
    return ImplementationSection(
        section=section,
        source=source[:MAX_SOURCE_CHARS],
        truncated=truncated,
    )


def _resolve_source_object(section: SourceSection) -> object:
    target = TRUSTED_SOURCE_TARGETS[section]
    if not isinstance(target, str):
        return target
    for route in sandbox_app.routes:
        if isinstance(route, APIRoute) and route.name == target:
            return route.endpoint
    raise LookupError(f"Approved route {target} is not registered")


def _failure(
    error_type: ToolErrorType,
    message: str,
) -> InspectEndpointImplementationResult:
    return InspectEndpointImplementationResult(
        success=False,
        error_type=error_type,
        error_message=message,
    )
