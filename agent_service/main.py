from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from fastapi import FastAPI
from openinference.semconv.trace import (
    OpenInferenceSpanKindValues,
    SpanAttributes,
)
from opentelemetry.context import Context
from opentelemetry.semconv.attributes.error_attributes import ERROR_TYPE
from opentelemetry.trace import Status, StatusCode, Tracer
from pydantic import BaseModel, ConfigDict

from agent_service.agent import (
    DebugDiagnosis,
    DebugSessionState,
    DecisionProvider,
    OpenAIDecisionProvider,
    run_agent,
)
from agent_service.agent.schemas import NonEmptyStr
from agent_service.observability import (
    TracerProvider,
    configure_tracing,
    get_tracer,
    shutdown_tracing,
)
from sandbox_api.db import Database

ProviderFactory = Callable[[], DecisionProvider]
TracingFactory = Callable[[], TracerProvider | None]


class DebugRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue: NonEmptyStr


async def run_debug_session(
    issue: str,
    provider_factory: ProviderFactory = OpenAIDecisionProvider,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    database: Database | None = None,
    tracer: Tracer | None = None,
) -> DebugDiagnosis:
    state = DebugSessionState(session_id=str(uuid4()), issue=issue)
    tracer = tracer if tracer is not None else get_tracer(None)
    with tracer.start_as_current_span(
        "DebugSession",
        context=Context(),
        attributes={
            SpanAttributes.OPENINFERENCE_SPAN_KIND: (
                OpenInferenceSpanKindValues.AGENT.value
            ),
            SpanAttributes.SESSION_ID: state.session_id,
            SpanAttributes.AGENT_NAME: "apilens-debug-agent",
        },
        record_exception=False,
        set_status_on_exception=False,
    ) as session_span:
        try:
            diagnosis = await run_agent(
                state,
                provider_factory(),
                transport=transport,
                database=database,
                tracer=tracer,
            )
        except Exception as error:
            session_span.set_attribute(ERROR_TYPE, type(error).__name__[:256])
            session_span.set_status(
                Status(StatusCode.ERROR, "Debug session execution failed")
            )
            raise

        final_attributes: dict[str, str | bool | int] = {
            SpanAttributes.OPENINFERENCE_SPAN_KIND: (
                OpenInferenceSpanKindValues.CHAIN.value
            ),
            SpanAttributes.SESSION_ID: state.session_id,
            "apilens.diagnosis.status": diagnosis.status.value,
            "apilens.diagnosis.evidence_count": len(diagnosis.evidence),
            "apilens.diagnosis.root_cause_present": diagnosis.root_cause is not None,
            "apilens.diagnosis.suggested_fix_present": (
                diagnosis.suggested_fix is not None
            ),
        }
        if diagnosis.affected_endpoint is not None:
            final_attributes["apilens.diagnosis.affected_endpoint"] = (
                diagnosis.affected_endpoint[:256]
            )
        with tracer.start_as_current_span(
            "final_diagnosis", attributes=final_attributes
        ):
            pass

        session_span.set_attributes(
            {
                "apilens.session.final_status": diagnosis.status.value,
                "apilens.session.decision_count": state.step_count,
                "apilens.session.tool_call_count": state.tool_call_count,
            }
        )
        return diagnosis


def create_app(
    provider_factory: ProviderFactory = OpenAIDecisionProvider,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    database: Database | None = None,
    tracing_factory: TracingFactory = configure_tracing,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        tracer_provider = tracing_factory()
        app.state.tracer_provider = tracer_provider
        app.state.tracer = get_tracer(tracer_provider)
        try:
            yield
        finally:
            shutdown_tracing(tracer_provider)

    app = FastAPI(title="APILens Agent Service", lifespan=lifespan)
    app.state.tracer = get_tracer(None)

    @app.post("/debug", response_model=DebugDiagnosis)
    async def debug(request: DebugRequest) -> DebugDiagnosis:
        return await run_debug_session(
            request.issue,
            provider_factory,
            transport=transport,
            database=database,
            tracer=app.state.tracer,
        )

    return app


app = create_app()
