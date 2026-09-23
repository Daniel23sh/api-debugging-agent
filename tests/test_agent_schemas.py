from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from agent_service.agent.schemas import (
    AgentDecision,
    DebugDiagnosis,
    DiagnosisStatus,
    EvidenceItem,
    EvidenceSource,
    FinalAnswerDecision,
    Observation,
    ToolCallDecision,
    ToolCallRecord,
)


def evidence() -> EvidenceItem:
    return EvidenceItem(source="api_execution", finding="POST /orders returned 500")


def test_evidence_and_diagnosis_enums_serialize_to_contract_values() -> None:
    assert [source.value for source in EvidenceSource] == [
        "api_spec",
        "api_execution",
        "server_logs",
        "endpoint_implementation",
    ]
    assert [status.value for status in DiagnosisStatus] == [
        "RESOLVED",
        "NO_BUG_FOUND",
        "INCONCLUSIVE",
        "LIMIT_REACHED",
    ]
    assert '"source":"api_execution"' in evidence().model_dump_json()


@pytest.mark.parametrize(
    "diagnosis",
    [
        DebugDiagnosis(
            status="RESOLVED",
            affected_endpoint="POST /orders",
            root_cause="Inventory lookup result is dereferenced when missing",
            evidence=[evidence()],
            suggested_fix="Handle a missing inventory record",
        ),
        DebugDiagnosis(status="NO_BUG_FOUND", evidence=[evidence()]),
        DebugDiagnosis(
            status="INCONCLUSIVE",
            evidence=[evidence()],
            missing_evidence=["request-correlated logs"],
        ),
        DebugDiagnosis(
            status="LIMIT_REACHED",
            missing_evidence=["endpoint implementation"],
        ),
    ],
)
def test_each_diagnosis_status_has_a_valid_shape(diagnosis: DebugDiagnosis) -> None:
    assert diagnosis.status.value in DiagnosisStatus


@pytest.mark.parametrize(
    "values",
    [
        {"status": "RESOLVED"},
        {"status": "NO_BUG_FOUND"},
        {
            "status": "NO_BUG_FOUND",
            "evidence": [evidence()],
            "root_cause": "claimed bug",
        },
        {"status": "INCONCLUSIVE"},
        {
            "status": "INCONCLUSIVE",
            "missing_evidence": ["logs"],
            "root_cause": "unsupported claim",
        },
        {"status": "LIMIT_REACHED"},
    ],
)
def test_invalid_diagnosis_status_combinations_are_rejected(values: dict) -> None:
    with pytest.raises(ValidationError):
        DebugDiagnosis.model_validate(values)


def test_diagnosis_rejects_extra_fields_and_has_independent_lists() -> None:
    with pytest.raises(ValidationError):
        DebugDiagnosis(
            status="INCONCLUSIVE",
            missing_evidence=["logs"],
            confidence=0.5,
        )

    first = DebugDiagnosis(status="INCONCLUSIVE", missing_evidence=["logs"])
    second = DebugDiagnosis(status="INCONCLUSIVE", missing_evidence=["code"])
    first.evidence.append(evidence())
    assert second.evidence == []


def test_both_decisions_validate_through_discriminated_union() -> None:
    adapter = TypeAdapter(AgentDecision)
    tool_call = adapter.validate_python(
        {
            "kind": "tool_call",
            "tool_name": "execute_api_request",
            "arguments": {"path": "/orders", "body": {"quantity": 1}},
        }
    )
    final_answer = adapter.validate_python(
        {
            "kind": "final_answer",
            "diagnosis": {
                "status": "INCONCLUSIVE",
                "missing_evidence": ["server logs"],
            },
        }
    )
    assert isinstance(tool_call, ToolCallDecision)
    assert isinstance(final_answer, FinalAnswerDecision)


@pytest.mark.parametrize(
    "decision",
    [
        {"tool_name": "inspect_api_spec", "arguments": {}},
        {"kind": "unknown", "tool_name": "inspect_api_spec", "arguments": {}},
        {
            "kind": "tool_call",
            "tool_name": "inspect_api_spec",
            "arguments": {},
            "diagnosis": {
                "status": "INCONCLUSIVE",
                "missing_evidence": ["logs"],
            },
        },
    ],
)
def test_invalid_or_ambiguous_decisions_are_rejected(decision: dict) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(AgentDecision).validate_python(decision)


def test_json_arguments_accept_json_and_reject_non_json_values() -> None:
    decision = ToolCallDecision(
        kind="tool_call",
        tool_name="execute_api_request",
        arguments={"values": [1, 2.5, True, None, {"key": "value"}]},
    )
    assert decision.model_dump_json()

    for invalid in (
        {"timestamp": datetime.now(UTC)},
        {"items": {1, 2}},
        {"n": float("nan")},
    ):
        with pytest.raises(ValidationError):
            ToolCallDecision(
                kind="tool_call",
                tool_name="execute_api_request",
                arguments=invalid,
            )


def test_tool_records_and_observations_are_json_serializable() -> None:
    call = ToolCallRecord(tool_name="execute_api_request", arguments={"path": "/orders"})
    success = Observation(
        tool_name="execute_api_request",
        success=True,
        data={"status_code": 500},
        request_id="request-1",
    )
    failure = Observation(
        tool_name="inspect_server_logs",
        success=False,
        error={"type": "LOG_NOT_FOUND", "message": "No matching request"},
    )
    assert call.model_dump_json()
    assert success.model_dump_json()
    assert failure.model_dump_json()

    with pytest.raises(ValidationError):
        Observation(
            tool_name="inspect_api_spec",
            success=True,
            data={"at": datetime.now(UTC)},
        )
