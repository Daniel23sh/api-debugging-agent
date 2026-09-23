import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, AsyncOpenAI
from pydantic import ValidationError

from agent_service.agent import OpenAIDecisionProvider
from agent_service.agent.loop import DecisionProvider, run_agent
from agent_service.agent.openai_provider import MAX_FUNCTION_OUTPUT_CHARS
from agent_service.agent.schemas import (
    DebugDiagnosis,
    FinalAnswerDecision,
    Observation,
    ToolCallDecision,
)
from agent_service.agent.state import DebugSessionState, SessionStatus
from sandbox_api.db import Database
from sandbox_api.main import create_app


def tool_response(response_id: str, name: str, arguments: dict | str, call_id: str):
    return SimpleNamespace(
        id=response_id,
        status="completed",
        output=[SimpleNamespace(
            type="function_call",
            name=name,
            arguments=arguments if isinstance(arguments, str) else json.dumps(arguments),
            call_id=call_id,
        )],
        output_parsed=None,
    )


def final_response(response_id: str, status: str = "INCONCLUSIVE"):
    diagnosis = (
        {
            "status": "RESOLVED",
            "affected_endpoint": "POST /orders",
            "root_cause": "Missing inventory is dereferenced",
            "evidence": [{"source": "server_logs", "finding": "Request failed with TypeError"}],
            "missing_evidence": [],
            "suggested_fix": "Handle missing inventory",
        }
        if status == "RESOLVED"
        else {
            "status": "NO_BUG_FOUND",
            "affected_endpoint": None,
            "root_cause": None,
            "evidence": [{"source": "api_spec", "finding": "Behavior matches contract"}],
            "missing_evidence": [],
            "suggested_fix": None,
        }
        if status == "NO_BUG_FOUND"
        else {
            "status": status,
            "affected_endpoint": None,
            "root_cause": None,
            "evidence": [],
            "missing_evidence": ["More evidence is needed"],
            "suggested_fix": None,
        }
    )
    return SimpleNamespace(
        id=response_id,
        status="completed",
        output=[SimpleNamespace(
            type="message",
            content=[SimpleNamespace(type="output_text")],
        )],
        output_parsed=diagnosis,
    )


class FakeResponses:
    def __init__(self, *replies) -> None:
        self.replies = iter(replies)
        self.requests = []

    async def parse(self, **kwargs):
        self.requests.append(kwargs)
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply(kwargs) if callable(reply) else reply


def provider(*replies, **config):
    responses = FakeResponses(*replies)
    client = SimpleNamespace(responses=responses)
    return OpenAIDecisionProvider(client=client, **config), responses


def state() -> DebugSessionState:
    return DebugSessionState(session_id="session-1", issue="POST /orders returns 500")


def assert_strict_objects(schema: dict) -> None:
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            assert schema.get("additionalProperties") is False
            assert set(schema.get("properties", {})) == set(schema.get("required", []))
        for value in schema.values():
            assert_strict_objects(value)
    elif isinstance(schema, list):
        for value in schema:
            assert_strict_objects(value)


@pytest.mark.asyncio
async def test_initial_request_is_provider_neutral_and_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-sol")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "medium")
    adapter, fake = provider(tool_response(
        "response-1", "execute_api_request",
        {"method": "post", "path": " /orders ", "body": '{"product_id":4,"quantity":1}'},
        "call-1",
    ))
    typed_provider: DecisionProvider = adapter
    assert callable(typed_provider.next_decision)
    decision = await adapter.next_decision(state())
    assert isinstance(decision, ToolCallDecision)
    assert decision.arguments == {
        "method": "post", "path": " /orders ",
        "body": {"product_id": 4, "quantity": 1},
    }
    request = fake.requests[0]
    assert request["input"] == "POST /orders returns 500"
    assert "previous_response_id" not in request
    assert request["model"] == "gpt-6-sol"
    assert request["reasoning"] == {"effort": "medium"}
    assert request["instructions"]
    assert request["parallel_tool_calls"] is False
    assert request["tool_choice"] == "auto"
    assert request["store"] is True
    assert issubclass(request["text_format"], DebugDiagnosis)
    tools = request["tools"]
    assert {item["name"] for item in tools} == {
        "inspect_api_spec", "execute_api_request", "inspect_server_logs",
        "inspect_endpoint_implementation",
    }
    assert all(item["type"] == "function" and item["strict"] for item in tools)
    assert all(item["description"] for item in tools)
    for item in tools:
        assert_strict_objects(item["parameters"])
    execute = next(item for item in tools if item["name"] == "execute_api_request")
    assert execute["parameters"]["properties"]["body"]["type"] == [
        "string", "null"
    ]
    assert "JsonValue" not in execute["parameters"].get("$defs", {})


@pytest.mark.asyncio
async def test_continuation_uses_matching_call_id_and_only_new_observations() -> None:
    adapter, fake = provider(
        tool_response("response-1", "inspect_api_spec", {"method": "GET", "path": "/products/1"}, "call-1"),
        final_response("response-2"),
    )
    current = state()
    current.observations.append(Observation(
        tool_name="execute_api_request", success=True, data={"status_code": 200}
    ))
    await adapter.next_decision(current)
    current.observations.append(Observation(
        tool_name="inspect_api_spec", success=True, data={"path": "/products/{product_id}"}
    ))
    answer = await adapter.next_decision(current)
    assert isinstance(answer, FinalAnswerDecision)
    request = fake.requests[1]
    assert request["previous_response_id"] == "response-1"
    assert request["instructions"] == fake.requests[0]["instructions"]
    assert request["tools"] == fake.requests[0]["tools"]
    assert request["input"][0]["type"] == "function_call_output"
    assert request["input"][0]["call_id"] == "call-1"
    output = json.loads(request["input"][0]["output"])
    assert len(output["observations"]) == 1
    assert output["observations"][0]["tool_name"] == "inspect_api_spec"
    assert current.model_dump()["observations"][0]["tool_name"] == "execute_api_request"


@pytest.mark.asyncio
async def test_timeout_and_retry_share_one_function_output() -> None:
    adapter, fake = provider(
        tool_response("response-1", "execute_api_request", {
            "method": "GET", "path": "/products/1", "body": None,
        }, "call-1"),
        final_response("response-2"),
    )
    current = state()
    await adapter.next_decision(current)
    current.observations.extend([
        Observation(tool_name="execute_api_request", success=False,
                    error={"type": "TOOL_TIMEOUT", "message": "slow"}),
        Observation(tool_name="execute_api_request", success=True,
                    data={"status_code": 200}, request_id="request-1"),
    ])
    await adapter.next_decision(current)
    request = fake.requests[1]
    assert len(request["input"]) == 1
    payload = json.loads(request["input"][0]["output"])
    assert [entry["success"] for entry in payload["observations"]] == [False, True]
    assert payload["observations"][1]["request_id"] == "request-1"


@pytest.mark.asyncio
async def test_large_function_output_preserves_outcomes_with_bounded_data() -> None:
    adapter, fake = provider(
        tool_response("response-1", "inspect_api_spec", {
            "method": "GET", "path": "/products/1",
        }, "call-1"),
        final_response("response-2"),
    )
    current = state()
    await adapter.next_decision(current)
    current.observations.append(Observation(
        tool_name="inspect_api_spec", success=True,
        data={"source": "x" * (MAX_FUNCTION_OUTPUT_CHARS + 1)},
        request_id="request-1",
    ))
    await adapter.next_decision(current)
    output = fake.requests[1]["input"][0]["output"]
    assert len(output) <= MAX_FUNCTION_OUTPUT_CHARS
    payload = json.loads(output)
    assert payload["data_truncated"] is True
    assert payload["observations"][0]["success"] is True
    assert payload["observations"][0]["request_id"] == "request-1"


@pytest.mark.asyncio
async def test_rejected_or_blocked_feedback_is_function_output() -> None:
    adapter, fake = provider(
        tool_response("response-1", "unknown", {}, "call-1"),
        final_response("response-2"),
    )
    current = state()
    await adapter.next_decision(current)
    current.observations.append(Observation(
        tool_name="unknown", success=False,
        error={"type": "TOOL_NOT_FOUND", "message": "not registered"},
    ))
    await adapter.next_decision(current)
    payload = json.loads(fake.requests[1]["input"][0]["output"])
    assert payload["observations"][0]["error"]["type"] == "TOOL_NOT_FOUND"


@pytest.mark.asyncio
async def test_model_arguments_still_pass_through_phase_three_b_validation() -> None:
    adapter, fake = provider(
        tool_response("response-1", "inspect_server_logs", {"request_id": "bad-uuid"}, "call-1"),
        final_response("response-2"),
    )
    current = state()
    diagnosis = await run_agent(current, adapter)
    assert diagnosis.status == "INCONCLUSIVE"
    assert current.step_count == 2 and current.tool_call_count == 0
    assert current.observations[0].error["type"] == "TOOL_ARGUMENT_VALIDATION_ERROR"
    payload = json.loads(fake.requests[1]["input"][0]["output"])
    assert payload["observations"][0]["error"]["type"] == (
        "TOOL_ARGUMENT_VALIDATION_ERROR"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["RESOLVED", "NO_BUG_FOUND", "INCONCLUSIVE"])
async def test_valid_final_diagnosis_statuses(status: str) -> None:
    adapter, _ = provider(final_response("response-1", status))
    decision = await adapter.next_decision(state())
    assert isinstance(decision, FinalAnswerDecision)
    assert decision.diagnosis.status == status


@pytest.mark.asyncio
async def test_model_limit_reached_is_rejected_then_corrected() -> None:
    adapter, fake = provider(
        final_response("response-1", "LIMIT_REACHED"),
        final_response("response-2", "INCONCLUSIVE"),
    )
    decision = await adapter.next_decision(state())
    assert decision.diagnosis.status == "INCONCLUSIVE"
    assert len(fake.requests) == 2
    assert "previous_response_id" not in fake.requests[1]
    assert "could not be accepted" in fake.requests[1]["input"][1]["content"]


@pytest.mark.asyncio
async def test_correction_resubmits_pending_output_from_last_accepted_response() -> None:
    adapter, fake = provider(
        tool_response("response-1", "inspect_api_spec", {
            "method": "GET", "path": "/products/1",
        }, "call-1"),
        tool_response("bad-response", "inspect_api_spec", "{", "bad-call"),
        final_response("response-2"),
    )
    current = state()
    await adapter.next_decision(current)
    current.observations.append(Observation(
        tool_name="inspect_api_spec", success=True, data={"path": "/products/1"},
    ))
    await adapter.next_decision(current)
    correction = fake.requests[2]
    assert correction["previous_response_id"] == "response-1"
    assert correction["input"][0]["type"] == "function_call_output"
    assert correction["input"][0]["call_id"] == "call-1"
    assert "could not be accepted" in correction["input"][1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    tool_response("bad", "inspect_api_spec", "{", "call-1"),
    SimpleNamespace(id="bad", status="completed", output=[
        tool_response("x", "inspect_api_spec", {}, "call-1").output[0],
        tool_response("y", "inspect_server_logs", {}, "call-2").output[0],
    ], output_parsed=None),
    SimpleNamespace(id="bad", status="completed", output=[
        tool_response("x", "inspect_api_spec", {}, "call-1").output[0],
        SimpleNamespace(
            type="message", phase="final_answer",
            content=[SimpleNamespace(type="output_text")],
        ),
    ], output_parsed=final_response("y").output_parsed),
    SimpleNamespace(id="bad", status="completed", output=None, output_parsed=None),
    SimpleNamespace(id="bad", status="completed", output=[SimpleNamespace(
        type="function_call", arguments="{}", call_id="call-1",
    )], output_parsed=None),
    final_response("bad", "LIMIT_REACHED"),
])
async def test_one_correction_accepts_valid_response_without_extra_decision(bad) -> None:
    adapter, fake = provider(bad, final_response("corrected"))
    current = state()
    diagnosis = await run_agent(current, adapter)
    assert diagnosis.status == "INCONCLUSIVE"
    assert current.step_count == 1
    assert current.tool_call_count == 0
    assert len(fake.requests) == 2


@pytest.mark.asyncio
async def test_second_invalid_output_is_typed_safe_failure() -> None:
    adapter, fake = provider(
        tool_response("bad-1", "inspect_api_spec", "{", "call-1"),
        tool_response("bad-2", "inspect_api_spec", "{", "call-2"),
    )
    current = state()
    diagnosis = await run_agent(current, adapter)
    assert diagnosis.status == "INCONCLUSIVE"
    assert current.step_count == current.tool_call_count == 0
    assert current.status is SessionStatus.COMPLETED
    assert current.observations[0].error["type"] == "MODEL_OUTPUT_VALIDATION_ERROR"
    assert len(fake.requests) == 2


@pytest.mark.asyncio
async def test_sdk_structured_parse_failure_gets_one_correction() -> None:
    try:
        DebugDiagnosis.model_validate({})
    except ValidationError as error:
        parse_error = error
    adapter, fake = provider(parse_error, final_response("corrected"))
    current = state()
    diagnosis = await run_agent(current, adapter)
    assert diagnosis.status == "INCONCLUSIVE"
    assert current.step_count == 1
    assert len(fake.requests) == 2
    assert "previous_response_id" not in fake.requests[1]
    assert fake.requests[1]["input"][0]["content"] == current.issue


@pytest.mark.asyncio
async def test_openai_api_error_is_typed_without_automatic_retry() -> None:
    adapter, fake = provider(APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/responses")))
    current = state()
    diagnosis = await run_agent(current, adapter)
    assert diagnosis.status == "INCONCLUSIVE"
    assert current.observations[0].error["type"] == "MODEL_API_ERROR"
    assert len(fake.requests) == 1


@pytest.mark.asyncio
async def test_real_sdk_parse_request_shape_uses_strict_text_format() -> None:
    requests = []
    diagnosis = final_response("sdk-response").output_parsed

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "sdk-response",
            "object": "response",
            "created_at": 0,
            "model": "gpt-6-sol",
            "status": "completed",
            "output": [{
                "type": "message", "id": "message-1", "status": "completed",
                "role": "assistant", "phase": "final_answer", "content": [{
                    "type": "output_text", "text": json.dumps(diagnosis),
                    "annotations": [],
                }],
            }],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AsyncOpenAI(api_key="test-key", http_client=http_client, max_retries=0)
        adapter = OpenAIDecisionProvider(client=client)
        decision = await adapter.next_decision(state())

    assert decision.diagnosis.status == "INCONCLUSIVE"
    assert len(requests) == 1
    wire = requests[0]
    assert wire["text"]["format"]["type"] == "json_schema"
    assert wire["text"]["format"]["strict"] is True
    assert_strict_objects(wire["text"]["format"]["schema"])
    assert wire["parallel_tool_calls"] is False
    assert {tool["name"] for tool in wire["tools"]} == {
        "inspect_api_spec", "execute_api_request", "inspect_server_logs",
        "inspect_endpoint_implementation",
    }


@pytest.mark.asyncio
async def test_real_sdk_commentary_and_one_function_call_is_one_decision() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "sdk-response",
            "object": "response",
            "created_at": 0,
            "model": "gpt-6-sol",
            "status": "completed",
            "output": [
                {"type": "reasoning", "id": "reasoning-1", "summary": []},
                {
                    "type": "message", "id": "message-1", "status": "completed",
                    "role": "assistant", "phase": "commentary",
                    "content": [{
                        "type": "output_text", "text": "Checking the API specification.",
                        "annotations": [],
                    }],
                },
                {
                    "type": "function_call", "id": "function-1", "status": "completed",
                    "name": "inspect_api_spec", "call_id": "call-1",
                    "arguments": '{"method":"GET","path":"/products/1"}',
                },
            ],
        })

    current = state()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AsyncOpenAI(api_key="test-key", http_client=http_client, max_retries=0)
        adapter = OpenAIDecisionProvider(client=client)
        decision = await adapter.next_decision(current)

    assert isinstance(decision, ToolCallDecision)
    assert decision.tool_name == "inspect_api_spec"
    assert len(requests) == 1
    assert current.step_count == current.tool_call_count == 0
    assert current.observations == []


@pytest.mark.asyncio
async def test_full_model_tool_log_diagnosis_trajectory(tmp_path: Path) -> None:
    path = tmp_path / "sandbox.db"
    database = Database(path)
    database.initialize()

    def logs_from_execution(request: dict):
        output = json.loads(request["input"][0]["output"])
        request_id = output["observations"][0]["request_id"]
        return tool_response(
            "response-2", "inspect_server_logs", {"request_id": request_id}, "call-2"
        )

    adapter, fake = provider(
        tool_response("response-1", "execute_api_request", {
            "method": "POST", "path": "/orders",
            "body": '{"product_id":4,"quantity":1}',
        }, "call-1"),
        logs_from_execution,
        final_response("response-3", "RESOLVED"),
    )
    current = state()
    diagnosis = await run_agent(
        current, adapter, database=database,
        transport=httpx.ASGITransport(app=create_app(path)),
    )
    assert diagnosis.status == "RESOLVED"
    assert current.step_count == 3 and current.tool_call_count == 2
    assert current.request_ids == [current.observations[0].request_id]
    assert current.tool_history[1].arguments["request_id"] == current.request_ids[0]
    assert current.observations[1].success
    assert current.status is SessionStatus.COMPLETED
    assert fake.requests[2]["previous_response_id"] == "response-2"
    assert fake.requests[2]["input"][0]["call_id"] == "call-2"
    assert "response-1" not in current.model_dump_json()
    assert "call-1" not in current.model_dump_json()


@pytest.mark.asyncio
async def test_alternate_model_trajectory_finishes_without_fixed_tool_order(tmp_path: Path) -> None:
    path = tmp_path / "sandbox.db"
    Database(path).initialize()
    adapter, _ = provider(
        tool_response("response-1", "inspect_api_spec", {
            "method": "GET", "path": "/products/1",
        }, "call-1"),
        final_response("response-2", "NO_BUG_FOUND"),
    )
    current = state()
    diagnosis = await run_agent(
        current, adapter, transport=httpx.ASGITransport(app=create_app(path))
    )
    assert diagnosis.status == "NO_BUG_FOUND"
    assert current.tool_call_count == 1
    assert current.tool_history[0].tool_name == "inspect_api_spec"
