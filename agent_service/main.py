from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from fastapi import FastAPI
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
) -> DebugDiagnosis:
    state = DebugSessionState(session_id=str(uuid4()), issue=issue)
    return await run_agent(
        state,
        provider_factory(),
        transport=transport,
        database=database,
    )


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

    @app.post("/debug", response_model=DebugDiagnosis)
    async def debug(request: DebugRequest) -> DebugDiagnosis:
        return await run_debug_session(
            request.issue,
            provider_factory,
            transport=transport,
            database=database,
        )

    return app


app = create_app()
