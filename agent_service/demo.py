"""Disposable demo fixtures and an allowlisted, memory-only execution timeline."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Literal
from uuid import UUID

import httpx
from opentelemetry.trace import get_current_span
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
)

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


class DemoExecutionMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    tool_attempt_count: int = Field(default=0, ge=0)
    model_request_count: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class DemoDebugResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diagnosis: DebugDiagnosis
    steps: list[InvestigationStep]
    metrics: DemoExecutionMetrics | None = None
    trace_id: str | None = Field(
        default=None, strict=True, min_length=32, max_length=32,
        pattern=r"^[0-9a-f]{32}$",
        description="Recording DebugSession trace ID; does not confirm Phoenix ingestion.",
    )

    @field_validator("trace_id")
    @classmethod
    def validate_trace_id(cls, value: str | None) -> str | None:
        if value == "0" * 32:
            raise ValueError("trace ID must be nonzero")
        return value


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
        self.trace_id: str | None = None
        self.provider_failed = False
        self._selected: InvestigationStep | None = None
        self.metrics = DemoExecutionMetrics()
        self._started_at: float | None = None
        self._model_usage: dict[str, dict[str, int | None] | None] = {}

    def start(self, state: DebugSessionState, limits: AgentLimits) -> None:
        self._started_at = monotonic()
        span = get_current_span()
        context = span.get_span_context()
        self.trace_id = (
            f"{context.trace_id:032x}"
            if span.is_recording() and context.is_valid else None
        )

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
        call_id = str(len(self._model_usage) + 1)
        self._model_usage[call_id] = None
        return call_id

    def model_finished(
        self,
        call_id: str,
        latency_seconds: float,
        usage: dict[str, int | None] | None,
        error_type: str | None,
    ) -> None:
        if call_id in self._model_usage and usage is not None:
            self._model_usage[call_id] = {
                key: value if type(value) is int and value >= 0 else None
                for key in ("input_tokens", "output_tokens", "total_tokens")
                for value in [usage.get(key)]
            }

    def finish(
        self, state: DebugSessionState, diagnosis: DebugDiagnosis | None
    ) -> None:
        self._selected = None
        tokens: dict[str, int | None] = {}
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            values = [(usage or {}).get(key) for usage in self._model_usage.values()]
            tokens[key] = (
                sum(value for value in values if value is not None)
                if values and all(value is not None for value in values) else None
            )
        self.metrics = DemoExecutionMetrics(
            duration_seconds=(
                monotonic() - self._started_at if self._started_at is not None else None
            ),
            tool_attempt_count=sum(step.kind == "tool_attempt" for step in self.steps),
            model_request_count=len(self._model_usage) or None,
            **tokens,
        )


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
