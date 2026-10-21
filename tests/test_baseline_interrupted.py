"""A published diagnosis does not commit a completed baseline execution."""

import pytest

from agent_service.agent.schemas import (
    DebugDiagnosis,
    FinalAnswerDecision,
    ToolCallDecision,
)
from evaluation import baseline
from evaluation.baseline_report import build_summary
from evaluation.trajectory import assess_success


class SuccessfulProvider:
    async def next_decision(self, state):
        case = next(
            case for case in baseline.cases() if case.user_report == state.issue
        )
        first = baseline.cases()[0]
        if (
            case.paraphrase_group == first.paraphrase_group
            and case.case_id != first.case_id
            and not state.observations
        ):
            request = case.reproduction_request
            return ToolCallDecision(
                kind="tool_call",
                tool_name="execute_api_request",
                arguments={
                    "method": request.method.value,
                    "path": request.path,
                    "body": request.body,
                },
            )
        values = {"status": case.expected_final_status.value}
        if case.case_kind == "bug":
            values.update(
                affected_endpoint=case.endpoint,
                root_cause="mock cause",
                suggested_fix="mock fix",
                evidence=[{"source": "api_execution", "finding": "mock evidence"}],
            )
        elif case.case_kind == "no_bug":
            values["evidence"] = [
                {"source": "api_execution", "finding": "mock evidence"}
            ]
        else:
            values["missing_evidence"] = ["mock missing evidence"]
        return FinalAnswerDecision(
            kind="final_answer", diagnosis=DebugDiagnosis(**values)
        )


class SuccessfulJudge:
    model = "mock-judge"

    async def judge(self, request, response_type):
        return response_type(
            label={
                "root_cause": "CORRECT",
                "grounding": "GROUNDED",
                "response_quality": "GOOD",
                "tool_selection": "APPROPRIATE",
            }[request.evaluator],
            explanation="mock judgment",
            evidence_refs=["user_report"] if request.evaluator == "grounding" else [],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_state", [None, "INTERRUPTED", "EXCEPTION", "RETURNED"]
)
async def test_success_requires_returned_execution(
    tmp_path, monkeypatch, terminal_state
):
    directory = tmp_path / "baseline"
    baseline.initialize(
        directory,
        baseline.BaselineConfig(
            agent_model="mock-agent",
            reasoning_effort="medium",
            judge_model="mock-judge",
        ),
        mode="offline",
    )
    first = baseline.cases()[0]
    location = baseline.case_directory(directory, first.case_id)
    write_private = baseline.write_private
    pending_execution = None

    def interrupt_terminal_publication(path, value):
        nonlocal pending_execution
        if path == location / "execution.json":
            pending_execution = value
            raise OSError("simulated interruption before terminal publication")
        write_private(path, value)

    if terminal_state != "RETURNED":
        with monkeypatch.context() as patch:
            patch.setattr(baseline, "write_private", interrupt_terminal_publication)
            with pytest.raises(OSError, match="before terminal publication"):
                await baseline.collect_runs(directory, SuccessfulProvider)
        assert (location / "started.json").exists()
        assert (location / "run.json").exists()
        assert not (location / "execution.json").exists()
        if terminal_state is not None:
            write_private(
                location / "execution.json",
                {
                    **pending_execution,
                    "state": terminal_state,
                    "error_type": "MOCK_INTERRUPTION",
                },
            )
    else:
        await baseline.collect_runs(directory, SuccessfulProvider)

    run = baseline.load_snapshot(directory, first.case_id)
    snapshot_bytes = (location / "run.json").read_bytes()
    await baseline.evaluate_saved(directory, SuccessfulJudge())
    bundle = baseline.load_bundle(directory, first.case_id, revision="initial")
    bundle_path = (
        baseline.evaluation_directory(directory, "initial")
        / first.case_id
        / "result.json"
    )
    bundle_bytes = bundle_path.read_bytes()
    assert run.diagnosis.status == first.expected_final_status
    assert assess_success(first, run, bundle.results).eligible
    assert bundle.convergence_eligibility.eligible

    summary = build_summary(directory)
    record = summary["cases"][0]
    returned = terminal_state == "RETURNED"
    assert record["execution_state"] == (terminal_state or "INTERRUPTED")
    assert record["success_eligibility"]["eligible"] is returned
    assert summary["successful_runs"] == (18 if returned else 0)
    assert summary["expected_status"]["matches"] == (18 if returned else 1)
    assert summary["measurements"]["latency_seconds"]["observed_cases"] == (
        18 if returned else 1
    )
    assert record["expected_status_match"] is True
    assert record["evaluation_state"] == "COMPLETE"
    assert record["decision_attempts"] == record["path_length"] == 1
    assert record["system_metrics"]["latency_seconds"] is not None
    assert record["evaluators"]["root_cause"]["labels"] == {"CORRECT": 1}
    assert record["evaluators"]["grounding"]["labels"] == {"GROUNDED": 1}
    if returned:
        assert record["convergence"]["ratio"] == 1
        assert summary["cases"][1]["convergence"]["ratio"] == 1 / 3
    if not returned:
        assert record["success_eligibility"]["reason"] == "EXECUTION_NOT_RETURNED"
        assert record["convergence"] is None

        await baseline.collect_runs(directory, SuccessfulProvider)
        await baseline.evaluate_saved(directory, SuccessfulJudge())
        summary = build_summary(directory)
        assert summary["successful_runs"] == 17
        assert summary["cases"][0]["convergence"] is None
        longer = summary["cases"][1]
        assert longer["path_length"] == 3
        assert longer["convergence"]["shortest_successful_observed_path"] == 3
        assert longer["convergence"]["ratio"] == 1
    assert (location / "run.json").read_bytes() == snapshot_bytes
    assert bundle_path.read_bytes() == bundle_bytes
