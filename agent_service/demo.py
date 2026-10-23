"""Disposable demo fixtures and an allowlisted, memory-only execution timeline."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from agent_service.agent.dispatch import DispatchErrorType
from agent_service.agent.loop import LoopFeedbackType
from agent_service.agent.schemas import (
    AgentDecision,
    DebugDiagnosis,
    Observation,
    ToolCallDecision,
    ToolCallRecord,
)
from agent_service.agent.state import AgentLimits, DebugSessionState
from agent_service.observability import safe_tool_metadata
from agent_service.tools.contracts import HttpMethod, ToolErrorType
from sandbox_api.db import Database
from sandbox_api.main import create_app as create_sandbox_app


class DemoScenario(StrEnum):
    MISSING_INVENTORY = "missing-inventory"
    WRONG_STATUS = "wrong-status"
    INCORRECT_TOTAL = "incorrect-total"
    RESPONSE_SCHEMA = "response-schema"


class DemoToolName(StrEnum):
    API_SPEC = "inspect_api_spec"
    API_REQUEST = "execute_api_request"
    SERVER_LOGS = "inspect_server_logs"
    IMPLEMENTATION = "inspect_endpoint_implementation"


DemoErrorCategory = (
    ToolErrorType | DispatchErrorType | LoopFeedbackType
    | Literal["PROVIDER_ERROR", "EXECUTION_ERROR", "CANCELLED"]
)
_ERROR_CATEGORY = TypeAdapter[DemoErrorCategory](DemoErrorCategory)


class InvestigationStep(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    kind: Literal["tool_attempt", "rejected_action", "session_error"]
    tool_name: DemoToolName | None = None
    method: HttpMethod | None = None
    route: Literal[
        "/products/{product_id}", "/inventory/{product_id}", "/orders",
        "/orders/{order_id}", "/payments",
    ] | None = None
    success: bool | None = None
    status_code: int | None = Field(default=None, ge=100, le=599, strict=True)
    request_id: UUID | None = None
    error_category: DemoErrorCategory | None = None
    retry: bool = False


class DemoDebugResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diagnosis: DebugDiagnosis
    steps: list[InvestigationStep]


def _safe_step(
    call: ToolCallRecord | ToolCallDecision,
    kind: Literal["tool_attempt", "rejected_action"],
    *,
    retry: bool = False,
) -> InvestigationStep:
    try:
        name = DemoToolName(call.tool_name)
    except ValueError:
        name = None
    metadata = safe_tool_metadata(call.arguments)
    return InvestigationStep.model_validate({
        "kind": kind,
        "tool_name": name,
        "method": metadata.get("method"),
        "route": metadata.get("path"),
        "request_id": metadata.get("request_id"),
        "retry": retry,
    })


class DemoRecorder:
    """Store only presentation metadata; never retain state, decisions or payloads."""

    def __init__(self) -> None:
        self.steps: list[InvestigationStep] = []
        self.provider_failed = False
        self._selected: InvestigationStep | None = None

    def start(self, state: DebugSessionState, limits: AgentLimits) -> None:
        pass

    def decision_started(self, state: DebugSessionState) -> None:
        self._selected = None

    def decision_returned(self, decision: AgentDecision) -> None:
        if isinstance(decision, ToolCallDecision):
            self._selected = _safe_step(decision, "rejected_action")

    def tool_started(self, call: ToolCallRecord, retry: bool) -> None:
        self.steps.append(_safe_step(call, "tool_attempt", retry=retry))

    def tool_span(self) -> None:
        pass

    def tool_finished(self, observation: Observation) -> None:
        step = self.steps[-1]
        step.success = observation.success
        metadata = safe_tool_metadata({"request_id": observation.request_id})
        if "request_id" in metadata:
            step.request_id = UUID(metadata["request_id"])
        if observation.success and observation.data is not None:
            status = observation.data.get("status_code")
            if type(status) is int and 100 <= status <= 599:
                step.status_code = status
        elif observation.error is not None:
            self.error("tool", str(observation.error.get("type", "")))

    def error(self, scope: str, error_type: str) -> None:
        if scope == "tool":
            step = self.steps[-1]
        elif self._selected is not None:
            step = self._selected
            self.steps.append(step)
        else:
            step = InvestigationStep(kind="session_error")
            self.steps.append(step)
            self.provider_failed = error_type not in {"SESSION_TIMEOUT", "CANCELLED"}
        step.success = False
        try:
            step.error_category = _ERROR_CATEGORY.validate_python(error_type)
        except ValidationError:
            step.error_category = (
                "PROVIDER_ERROR" if self.provider_failed else "EXECUTION_ERROR"
            )

    def model_started(self, model: str) -> str:
        return ""

    def model_finished(
        self,
        call_id: str,
        latency_seconds: float,
        usage: dict[str, int | None] | None,
        error_type: str | None,
    ) -> None:
        pass

    def finish(
        self, state: DebugSessionState, diagnosis: DebugDiagnosis | None
    ) -> None:
        self._selected = None


@asynccontextmanager
async def demo_sandbox(
    scenario: DemoScenario | None,
) -> AsyncIterator[tuple[Database, httpx.ASGITransport]]:
    with TemporaryDirectory(prefix="apilens-demo-") as temporary:
        path = Path(temporary) / "sandbox.db"
        database = Database(path)
        # ASGITransport does not run the Sandbox lifespan that normally seeds it.
        database.initialize()
        if scenario is DemoScenario.RESPONSE_SCHEMA:
            database.create_order(product_id=1, quantity=1)
        async with httpx.ASGITransport(app=create_sandbox_app(path)) as transport:
            yield database, transport
