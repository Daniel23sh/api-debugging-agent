from pathlib import Path
from uuid import UUID

import httpx
from fastapi.testclient import TestClient

from agent_service.agent import (
    AgentDecision,
    DebugDiagnosis,
    DebugSessionState,
    FinalAnswerDecision,
    ToolCallDecision,
)
from agent_service.main import create_app as create_agent_app
from sandbox_api.db import Database
from sandbox_api.ground_truth import BUG_GROUND_TRUTH, BugGroundTruth
from sandbox_api.main import create_app as create_sandbox_app
from sandbox_api.source_map import SourceSection


class StateAwareProvider:
    def __init__(self, case: BugGroundTruth) -> None:
        self.case = case
        self.states: list[DebugSessionState] = []

    async def next_decision(self, state: DebugSessionState) -> AgentDecision:
        self.states.append(state)
        assert all(observation.success for observation in state.observations)
        observations = {
            observation.tool_name: observation for observation in state.observations
        }

        if "execute_api_request" not in observations:
            return ToolCallDecision(
                kind="tool_call",
                tool_name="execute_api_request",
                arguments={
                    "method": self.case.method,
                    "path": self.case.request_path,
                    "body": self.case.request_body,
                },
            )
        if "inspect_server_logs" not in observations:
            return ToolCallDecision(
                kind="tool_call",
                tool_name="inspect_server_logs",
                arguments={"request_id": state.request_ids[-1]},
            )
        if "inspect_endpoint_implementation" not in observations:
            return ToolCallDecision(
                kind="tool_call",
                tool_name="inspect_endpoint_implementation",
                arguments={"method": self.case.method, "path": self.case.endpoint},
            )

        execution = observations["execute_api_request"]
        logs = observations["inspect_server_logs"]
        implementation = observations["inspect_endpoint_implementation"]
        failure = logs.data["events"][-1]
        database_source = next(
            section["source"]
            for section in implementation.data["sections"]
            if section["section"] == SourceSection.CREATE_ORDER_DATABASE
        )
        assert execution.data["status_code"] == self.case.actual_status
        assert failure["error_type"] == "TypeError"
        assert 'inventory["quantity"]' in database_source

        request_id = execution.request_id
        return FinalAnswerDecision(
            kind="final_answer",
            diagnosis=DebugDiagnosis(
                status="RESOLVED",
                affected_endpoint=f"{self.case.method} {self.case.endpoint}",
                root_cause=self.case.root_cause,
                evidence=[
                    {
                        "source": "api_execution",
                        "finding": (
                            f"Sandbox returned HTTP {execution.data['status_code']}"
                        ),
                        "reference": request_id,
                    },
                    {
                        "source": "server_logs",
                        "finding": (
                            f"{failure['error_type']}: {failure['error_message']}"
                        ),
                        "reference": request_id,
                    },
                    {
                        "source": "endpoint_implementation",
                        "finding": (
                            "Database.create_order indexes the missing inventory row"
                        ),
                    },
                ],
                suggested_fix=(
                    "Return 404 when the inventory row is missing before indexing it"
                ),
            ),
        )


def test_debug_http_request_runs_complete_sandbox_investigation(
    tmp_path: Path,
) -> None:
    case = next(
        case
        for case in BUG_GROUND_TRUTH
        if case.bug_id == "ORDER_INVENTORY_NULL_001"
    )
    database_path = tmp_path / "sandbox.db"
    database = Database(database_path)
    database.initialize()
    transport = httpx.ASGITransport(app=create_sandbox_app(database_path))
    providers: list[StateAwareProvider] = []

    def provider_factory() -> StateAwareProvider:
        provider = StateAwareProvider(case)
        providers.append(provider)
        return provider

    with TestClient(
        create_agent_app(
            provider_factory,
            transport=transport,
            database=database,
        )
    ) as client:
        response = client.post(
            "/debug",
            json={
                "issue": (
                    f"{case.method} {case.request_path} returns "
                    f"{case.actual_status} for {case.request_body}"
                )
            },
        )

    assert response.status_code == 200
    diagnosis = DebugDiagnosis.model_validate(response.json())
    assert diagnosis.status == "RESOLVED"
    assert diagnosis.root_cause == case.root_cause
    assert {item.source for item in diagnosis.evidence} == {
        "api_execution",
        "server_logs",
        "endpoint_implementation",
    }

    assert len(providers) == 1
    states = providers[0].states
    assert [state.step_count for state in states] == [0, 1, 2, 3]
    assert [len(state.observations) for state in states] == [0, 1, 2, 3]
    final_state = states[-1]
    assert [call.tool_name for call in final_state.tool_history] == [
        "execute_api_request",
        "inspect_server_logs",
        "inspect_endpoint_implementation",
    ]

    execution, logs, implementation = final_state.observations
    assert execution.data["status_code"] == case.actual_status == 500
    assert execution.data["response_body"] == {"detail": "Internal Server Error"}
    request_id = execution.request_id
    assert str(UUID(request_id)) == request_id
    assert final_state.request_ids == [request_id]
    assert final_state.tool_history[1].arguments == {"request_id": request_id}

    assert logs.data["request_id"] == request_id
    assert logs.data["events"][-1]["event_type"] == "request_failed"
    assert logs.data["events"][-1]["error_type"] == "TypeError"
    assert "NoneType" in logs.data["events"][-1]["error_message"]
    assert database.get_request_logs(request_id)[-1].error_type == "TypeError"

    source_by_section = {
        section["section"]: section["source"]
        for section in implementation.data["sections"]
    }
    assert (
        'inventory["quantity"]'
        in source_by_section[SourceSection.CREATE_ORDER_DATABASE]
    )
