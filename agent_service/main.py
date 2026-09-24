from collections.abc import Callable
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
from sandbox_api.db import Database

ProviderFactory = Callable[[], DecisionProvider]


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
) -> FastAPI:
    app = FastAPI(title="APILens Agent Service")

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
