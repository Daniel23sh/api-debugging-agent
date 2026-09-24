from fastapi.testclient import TestClient

from agent_service.agent import (
    DebugDiagnosis,
    DebugSessionState,
    FinalAnswerDecision,
)
from agent_service.main import create_app


class FakeProvider:
    def __init__(self) -> None:
        self.states: list[DebugSessionState] = []

    async def next_decision(
        self, state: DebugSessionState
    ) -> FinalAnswerDecision:
        self.states.append(state)
        return FinalAnswerDecision(
            kind="final_answer",
            diagnosis=DebugDiagnosis(
                status="INCONCLUSIVE",
                missing_evidence=["More evidence is needed"],
            ),
        )


def client_with_providers() -> tuple[TestClient, list[FakeProvider]]:
    providers: list[FakeProvider] = []

    def provider_factory() -> FakeProvider:
        provider = FakeProvider()
        providers.append(provider)
        return provider

    return TestClient(create_app(provider_factory)), providers


def test_debug_accepts_free_form_issue_and_returns_existing_diagnosis() -> None:
    client, providers = client_with_providers()

    response = client.post(
        "/debug",
        json={"issue": "  POST /orders returns 500\nwhen ordering product 4  "},
    )

    assert response.status_code == 200
    assert DebugDiagnosis.model_validate(response.json()).status == "INCONCLUSIVE"
    assert len(providers) == 1
    assert len(providers[0].states) == 1
    assert providers[0].states[0].issue == (
        "POST /orders returns 500\nwhen ordering product 4"
    )


def test_debug_rejects_empty_issue() -> None:
    client, _ = client_with_providers()

    for issue in ("", " \n\t "):
        assert client.post("/debug", json={"issue": issue}).status_code == 422


def test_debug_rejects_unexpected_fields() -> None:
    client, providers = client_with_providers()

    response = client.post(
        "/debug",
        json={"issue": "GET /products/1 fails", "endpoint": "/products/1"},
    )

    assert response.status_code == 422
    assert providers == []


def test_debug_requests_use_distinct_sessions_and_providers() -> None:
    client, providers = client_with_providers()

    assert client.post("/debug", json={"issue": "first issue"}).status_code == 200
    assert client.post("/debug", json={"issue": "second issue"}).status_code == 200

    assert len(providers) == 2
    assert providers[0] is not providers[1]
    assert providers[0].states[0].session_id != providers[1].states[0].session_id


def test_openapi_exposes_debug_request_and_diagnosis_response() -> None:
    schema = create_app(lambda: FakeProvider()).openapi()

    assert set(schema["paths"]) == {"/debug"}
    operation = schema["paths"]["/debug"]
    assert set(operation) == {"post"}
    post = operation["post"]
    request_schema = schema["components"]["schemas"]["DebugRequest"]
    assert post["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DebugRequest"
    }
    assert request_schema["required"] == ["issue"]
    assert set(request_schema["properties"]) == {"issue"}
    assert request_schema["additionalProperties"] is False
    assert post["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DebugDiagnosis"
    }
