import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from openinference.instrumentation import TracerProvider
from openinference.semconv.trace import (
    OpenInferenceSpanKindValues,
    SpanAttributes,
)
from opentelemetry.context import Context
from opentelemetry.semconv.attributes.error_attributes import ERROR_TYPE
from opentelemetry.trace import Status, StatusCode, Tracer
from pydantic import BaseModel, ConfigDict, Field

from agent_service.agent import (
    DebugDiagnosis,
    DebugSessionState,
    DecisionProvider,
    OpenAIDecisionProvider,
    run_agent,
)
from agent_service.agent.recording import SessionRecorder
from agent_service.agent.schemas import DiagnosisStatus, NonEmptyStr
from agent_service.demo import (
    DemoDebugResponse,
    DemoRecorder,
    DemoScenario,
    demo_sandbox,
)
from agent_service.observability import (
    configure_openai_instrumentation,
    configure_tracing,
    get_tracer,
    safe_diagnosis_endpoint,
    shutdown_openai_instrumentation,
    shutdown_tracing,
)
from sandbox_api.db import Database

ProviderFactory = Callable[[], DecisionProvider]
TracingFactory = Callable[[], TracerProvider | None]
MAX_ISSUE_CHARS = 4000


class DebugRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue: NonEmptyStr = Field(
        max_length=MAX_ISSUE_CHARS,
        description="Bug report, up to 4,000 characters after trimming outer whitespace.",
    )


class DemoDebugRequest(DebugRequest):
    scenario_id: DemoScenario | None = None


async def run_debug_session(
    issue: str,
    provider_factory: ProviderFactory = OpenAIDecisionProvider,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    database: Database | None = None,
    tracer: Tracer | None = None,
    recorder: SessionRecorder | None = None,
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
                recorder=recorder,
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
        endpoint = safe_diagnosis_endpoint(diagnosis.affected_endpoint)
        if endpoint is not None:
            final_attributes["apilens.diagnosis.affected_endpoint"] = endpoint
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
        openai_instrumentor = None
        try:
            openai_instrumentor = configure_openai_instrumentation(tracer_provider)
            app.state.tracer_provider = tracer_provider
            app.state.tracer = get_tracer(tracer_provider)
            app.state.openai_instrumentor = openai_instrumentor
            yield
        finally:
            try:
                shutdown_openai_instrumentation(openai_instrumentor)
            finally:
                shutdown_tracing(tracer_provider)

    app = FastAPI(title="APILens Agent Service", lifespan=lifespan)
    app.state.tracer = get_tracer(None)
    app.state.openai_instrumentor = None

    @app.post("/debug", response_model=DebugDiagnosis)
    async def debug(request: DebugRequest) -> DebugDiagnosis:
        return await run_debug_session(
            request.issue,
            provider_factory,
            transport=transport,
            database=database,
            tracer=app.state.tracer,
        )

    @app.post("/demo/debug", response_model=DemoDebugResponse)
    async def demo_debug(request: DemoDebugRequest) -> DemoDebugResponse:
        if (
            provider_factory is OpenAIDecisionProvider
            and not os.getenv("OPENAI_API_KEY", "").strip()
        ):
            raise HTTPException(
                status_code=503,
                detail="Demo debugging requires OPENAI_API_KEY on the Agent Service.",
            )
        recorder = DemoRecorder()
        try:
            async with demo_sandbox(request.scenario_id) as (demo_database, demo_transport):
                diagnosis = await run_debug_session(
                    request.issue,
                    provider_factory,
                    transport=demo_transport,
                    database=demo_database,
                    tracer=app.state.tracer,
                    recorder=recorder,
                )
        except Exception:  # noqa: BLE001 - keep private failures out of HTTP responses
            raise HTTPException(
                status_code=502, detail="Demo investigation failed."
            ) from None
        if recorder.provider_failed:
            raise HTTPException(
                status_code=502, detail="Demo model provider failed."
            )
        if diagnosis.status is DiagnosisStatus.LIMIT_REACHED:
            # The loop's fallback evidence contains raw tool data, not presentation text.
            diagnosis = DebugDiagnosis(
                status=DiagnosisStatus.LIMIT_REACHED,
                missing_evidence=(
                    diagnosis.missing_evidence or ["Investigation limit reached"]
                ),
            )
        return DemoDebugResponse(diagnosis=diagnosis, steps=recorder.steps)

    return app


app = create_app()
