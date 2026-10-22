"""Offline system contracts, not measurements of live model diagnosis quality."""

import json
import socket
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError

from agent_service.agent.loop import EVIDENCE_SOURCE_BY_TOOL
from agent_service.agent.schemas import (
    DebugDiagnosis,
    FinalAnswerDecision,
    ToolCallDecision,
)
from agent_service.main import run_debug_session
from evaluation.dataset import load_evaluation_cases
from evaluation.qualitative import evaluate_root_cause
from evaluation.recorder import EvaluationRecorder
from evaluation.tool_evaluators import evaluate_tool_arguments, evaluate_tool_selection
from sandbox_api.db import Database
from sandbox_api.main import create_app
from sandbox_api.source_map import SourceSection

pytestmark = [pytest.mark.golden, pytest.mark.asyncio]

CASES = {
    case.case_id: case
    for case in load_evaluation_cases(
        Path(__file__).parents[1] / "evaluation/datasets/v1_cases.json"
    )
}
GOLDEN_IDS = (
    "inventory_validation_zero_01",
    "product_missing_status_01",
    "order_total_quantity_01",
    "order_missing_inventory_02",
    "order_response_schema_01",
    "order_create_status_no_bug",
    "insufficient_report_no_endpoint",
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Golden tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    for name in ("OPENAI_API_KEY", "PHOENIX_COLLECTOR_ENDPOINT", "EVAL_JUDGE_MODEL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def sandbox(tmp_path):
    path = tmp_path / "sandbox.db"
    database = Database(path)
    # ASGITransport does not run the application's lifespan.
    database.initialize()
    return database, httpx.ASGITransport(app=create_app(path))


def tool(name, **arguments):
    return ToolCallDecision(kind="tool_call", tool_name=name, arguments=arguments)


class GoldenProvider:
    """Choose local probes; only finalize after observed contracts are checked."""

    def __init__(self, case, database):
        self.case = case
        self.database = database
        self.states = []

    async def next_decision(self, state):
        self.states.append(state)
        case = self.case
        if case.reproduction_request is None:
            assert case.endpoint is None and not state.observations
            return FinalAnswerDecision(
                kind="final_answer",
                diagnosis=DebugDiagnosis(
                    status="INCONCLUSIVE",
                    missing_evidence=[
                        "Provide the operation, request, response or request ID"
                    ],
                ),
            )

        assert all(item.success for item in state.observations)
        observations = {item.tool_name: item for item in state.observations}
        request = case.reproduction_request
        operation = {"method": request.method.value, "path": request.path}
        if "inspect_api_spec" not in observations:
            return tool("inspect_api_spec", **operation)
        if "execute_api_request" not in observations:
            return tool("execute_api_request", **operation, body=request.body)
        if (
            case.category == "runtime_exception"
            and "inspect_server_logs" not in observations
        ):
            return tool("inspect_server_logs", request_id=state.request_ids[-1])
        if (
            case.case_kind == "bug"
            and "inspect_endpoint_implementation" not in observations
        ):
            return tool("inspect_endpoint_implementation", **operation)

        assert_observed_behavior(case, observations, self.database)
        evidence = [
            {
                "source": EVIDENCE_SOURCE_BY_TOOL[item.tool_name],
                "finding": json.dumps(item.data, sort_keys=True),
                "reference": item.request_id,
            }
            for item in state.observations
        ]
        # Canonical text tests structured acceptance only; it is not a judged answer.
        return FinalAnswerDecision(
            kind="final_answer",
            diagnosis=DebugDiagnosis(
                status=case.expected_final_status,
                affected_endpoint=f"{request.method.value} {case.endpoint}",
                root_cause=case.expected_root_cause,
                suggested_fix="Align the implementation with the documented contract"
                if case.case_kind == "bug"
                else None,
                evidence=evidence,
            ),
        )


def assert_observed_behavior(case, observations, database):
    execution = observations["execute_api_request"]
    spec = observations["inspect_api_spec"].data
    data = execution.data
    body = data["response_body"]
    assert data["status_code"] == case.actual_status
    assert data["response_body_truncated"] is False
    assert str(UUID(execution.request_id)) == execution.request_id
    assert data["request_id"] == execution.request_id
    assert spec["path"] == case.endpoint
    assert spec["method"] == case.reproduction_request.method.value
    sections = (
        {
            section["section"]: section["source"]
            for section in observations["inspect_endpoint_implementation"].data[
                "sections"
            ]
        }
        if "inspect_endpoint_implementation" in observations
        else {}
    )

    if case.category == "validation_mismatch":
        quantity = spec["schemas"]["InventoryUpdate"]["properties"]["quantity"]
        assert quantity["minimum"] == 0 and "exclusiveMinimum" not in quantity
        assert any(
            error["loc"] == ["body", "quantity"] and error["type"] == "greater_than"
            for error in body["detail"]
        )
        assert database.get_inventory(1).quantity == 10
        assert "gt=0" in sections[SourceSection.INVENTORY_UPDATE_MODEL]
    elif case.category == "wrong_status_or_response":
        assert database.get_product(999) is None
        assert body == {}
        assert "id" in spec["schemas"]["Product"]["required"]
        assert "status_code=200" in sections[SourceSection.GET_PRODUCT_ROUTE]
    elif case.category == "business_logic":
        price = database.get_product(2).price
        assert price == Decimal("129.50")
        assert body["quantity"] == 2
        assert Decimal(body["total"]) == price != price * body["quantity"]
        assert database.get_order(body["id"]).total == price
        assert database.get_inventory(2).quantity == 3
        assert "quantity" in sections[SourceSection.CREATE_ORDER_DATABASE]
    elif case.category == "runtime_exception":
        assert database.get_product(4) is not None
        assert database.get_inventory(4) is None
        assert database.get_order(1) is None
        assert body == {"detail": "Internal Server Error"}
        logs = observations["inspect_server_logs"]
        assert logs.request_id == logs.data["request_id"] == execution.request_id
        events = logs.data["events"]
        assert [item["event_type"] for item in events] == [
            "request_received",
            "request_failed",
        ]
        assert all(
            item["method"] == "POST" and item["path"] == "/orders" for item in events
        )
        failure = events[-1]
        assert failure["status_code"] == data["status_code"] == 500
        assert failure["error_type"] == "TypeError"
        assert "NoneType" in failure["error_message"]
        assert (
            database.get_request_logs(execution.request_id)[-1].error_message
            == failure["error_message"]
        )
        assert 'inventory["quantity"]' in sections[SourceSection.CREATE_ORDER_DATABASE]
    elif case.category == "field_schema_mismatch":
        assert "product_id" in spec["schemas"]["Order"]["required"]
        assert body["id"] == database.get_order(1).id == 1
        assert body["productId"] == database.get_order(1).product_id == 1
        assert "product_id" not in body
        assert "JSONResponse" in sections[SourceSection.GET_ORDER_ROUTE]
    else:
        assert case.case_id == "order_create_status_no_bug"
        assert str(data["status_code"]) in spec["documented_status_codes"]
        assert data["status_code"] == case.expected_status == 201
        assert set(spec["schemas"]["Order"]["required"]) <= body.keys()
        assert body["id"] == 1 and body["status"] == "created"
        assert body["product_id"] == body["quantity"] == 1
        assert Decimal(body["total"]) == database.get_order(1).total == Decimal("79.99")
        assert database.get_inventory(1).quantity == 9


@pytest.mark.parametrize("case_id", GOLDEN_IDS)
async def test_golden_case_contracts_and_reset(case_id, sandbox):
    case = CASES[case_id]
    database, transport = sandbox
    previous_ids = set()
    session_ids = set()
    for _ in range(2):
        database.reset()
        assert [database.get_inventory(i).quantity for i in (1, 2, 3)] == [10, 5, 0]
        assert database.get_order(1) is None
        assert all(
            not database.get_request_logs(request_id) for request_id in previous_ids
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://sandbox"
        ) as client:
            for request in case.setup_requests:
                response = await client.request(
                    request.method.value, request.path, json=request.body
                )
                assert response.status_code == 201
                assert response.json()["id"] == 1

        provider = GoldenProvider(case, database)
        recorder = EvaluationRecorder(case.case_id)
        diagnosis = await run_debug_session(
            case.user_report,
            lambda provider=provider: provider,
            transport=transport,
            database=database,
            recorder=recorder,
        )
        run = recorder.snapshot()
        assert run.session_id not in session_ids
        session_ids.add(run.session_id)
        assert diagnosis.status == case.expected_final_status
        assert run.diagnosis == DebugDiagnosis.model_validate(diagnosis.model_dump())
        assert not run.errors and not run.model_calls
        assert provider.states[0].observations == provider.states[0].tool_history == []
        assert provider.states[0].request_ids == []
        assert provider.states[-1].tool_call_count == len(run.tool_attempts)
        assert provider.states[-1].step_count == len(run.decisions) - 1
        known = set()
        for decision in run.decisions:
            assert set(decision.prior_observation_ids) == known
            for attempt in run.tool_attempts:
                if attempt.decision_id == decision.decision_id:
                    assert not attempt.timeout_retry
                    known.add(attempt.observation_id)
            if isinstance(decision.decision, ToolCallDecision):
                assert evaluate_tool_arguments(case, run, decision).label == "CORRECT"
                if decision.decision.tool_name == "inspect_server_logs":
                    assert (
                        evaluate_tool_selection(case, run, decision).label
                        == "APPROPRIATE"
                    )
        assert known == {item.observation_id for item in run.observations}
        request_ids = set(provider.states[-1].request_ids)
        assert request_ids.isdisjoint(previous_ids)
        previous_ids.update(request_ids)
        for evidence in diagnosis.evidence:
            assert any(
                evidence.source == EVIDENCE_SOURCE_BY_TOOL[item.observation.tool_name]
                and json.loads(evidence.finding) == item.observation.data
                and evidence.reference == item.observation.request_id
                for item in run.observations
            )
        if case.case_kind != "bug":
            assert diagnosis.root_cause is diagnosis.suggested_fix is None
        if case.case_kind == "inconclusive":
            assert diagnosis.missing_evidence and not diagnosis.evidence
            assert not run.tool_attempts and not run.observations


@pytest.mark.parametrize(
    "call,error_type,executed",
    [
        (
            tool("inspect_server_logs", request_id="not-a-uuid"),
            "TOOL_ARGUMENT_VALIDATION_ERROR",
            False,
        ),
        (
            tool(
                "execute_api_request",
                method="POST",
                path="/orders",
                body={},
                extra=True,
            ),
            "TOOL_ARGUMENT_VALIDATION_ERROR",
            False,
        ),
        (
            tool(
                "execute_api_request",
                method="POST",
                path="https://example.invalid/orders",
                body={},
            ),
            "ENDPOINT_NOT_ALLOWED",
            True,
        ),
        (
            tool("inspect_server_logs", request_id=str(UUID(int=0))),
            "LOG_NOT_FOUND",
            True,
        ),
    ],
    ids=[
        "malformed-request-id",
        "extra-argument",
        "external-endpoint",
        "unknown-request-id",
    ],
)
async def test_golden_rejected_probe_remains_inconclusive(
    call, error_type, executed, sandbox
):
    database, transport = sandbox
    case = CASES["order_missing_inventory_02"]

    class Provider:
        async def next_decision(self, state):
            if not state.observations:
                return call
            assert len(state.observations) == 1
            assert state.observations[0].error["type"] == error_type
            assert not state.request_ids
            return FinalAnswerDecision(
                kind="final_answer",
                diagnosis=DebugDiagnosis(
                    status="INCONCLUSIVE", missing_evidence=["Probe failed"]
                ),
            )

    recorder = EvaluationRecorder(case.case_id)
    diagnosis = await run_debug_session(
        case.user_report,
        Provider,
        transport=transport,
        database=database,
        recorder=recorder,
    )
    run = recorder.snapshot()
    assert diagnosis.status == "INCONCLUSIVE" and not diagnosis.evidence
    assert len(run.tool_attempts) == int(executed)
    assert not run.observations[0].observation.success
    assert evaluate_tool_arguments(case, run, run.decisions[0]).label == "INCORRECT"
    assert database.get_order(1) is None
    assert database.get_inventory(1).quantity == 10


async def test_golden_log_evaluator_rejects_uncorrelated_request_id(sandbox):
    database, transport = sandbox
    case = CASES["order_missing_inventory_02"]
    recorder = EvaluationRecorder(case.case_id)
    await run_debug_session(
        case.user_report,
        lambda: GoldenProvider(case, database),
        transport=transport,
        database=database,
        recorder=recorder,
    )
    run = recorder.snapshot()
    decision = next(
        item
        for item in run.decisions
        if getattr(item.decision, "tool_name", None) == "inspect_server_logs"
    )
    assert evaluate_tool_arguments(case, run, decision).label == "CORRECT"
    decision.decision.arguments["request_id"] = str(uuid4())
    assert evaluate_tool_arguments(case, run, decision).label == "INCORRECT"


async def test_golden_non_bug_outcomes_and_status_validation(sandbox):
    class NoJudge:
        async def judge(self, *args):
            pytest.fail("Non-bug root-cause gates must not need a judge")

    database, transport = sandbox
    for case_id in ("order_create_status_no_bug", "insufficient_report_no_endpoint"):
        database.reset()
        case = CASES[case_id]
        recorder = EvaluationRecorder(case_id)
        await run_debug_session(
            case.user_report,
            lambda case=case: GoldenProvider(case, database),
            transport=transport,
            database=database,
            recorder=recorder,
        )
        run = recorder.snapshot()
        assert (
            await evaluate_root_cause(case, run, NoJudge())
        ).state == "NOT_APPLICABLE"
        # A root-cause claim for either non-bug case must be rejected deterministically.
        run.diagnosis = DebugDiagnosis(
            status="RESOLVED",
            affected_endpoint="POST /orders",
            root_cause="Invented bug",
            suggested_fix="Invented fix",
            evidence=[{"source": "api_spec", "finding": "Claim"}],
        )

        assert (await evaluate_root_cause(case, run, NoJudge())).label == "INCORRECT"

    for values in (
        {"status": "NO_BUG_FOUND"},
        {
            "status": "NO_BUG_FOUND",
            "root_cause": "Bug",
            "evidence": [{"source": "api_spec", "finding": "Claim"}],
        },
        {
            "status": "NO_BUG_FOUND",
            "suggested_fix": "Fix",
            "evidence": [{"source": "api_spec", "finding": "Claim"}],
        },
        {"status": "INCONCLUSIVE"},
        {
            "status": "INCONCLUSIVE",
            "root_cause": "Bug",
            "missing_evidence": ["Request"],
        },
    ):
        with pytest.raises(ValidationError):
            DebugDiagnosis.model_validate(values)
