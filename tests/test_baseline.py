import asyncio
import json
import os
import shutil
from collections import Counter
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent_service.agent.openai_provider import OpenAIDecisionProvider
from agent_service.agent.schemas import (
    DebugDiagnosis,
    FinalAnswerDecision,
    ToolCallDecision,
)
from agent_service.observability import configure_tracing, get_tracer, shutdown_tracing
from evaluation import baseline
from evaluation.baseline import (
    BaselineConfig,
    BaselineError,
    OfflineJudge,
    case_directory,
    cases,
    collect_runs,
    evaluate_saved,
    initialize,
    load_manifest,
    load_snapshot,
    write_private,
)
from evaluation.baseline_report import build_summary, render_report, save_report
from evaluation.judges import JudgeError

CONFIG = BaselineConfig(
    agent_model="mock-agent", reasoning_effort="medium", judge_model="mock-judge"
)
CASES = {case.user_report: case for case in cases()}


def final(case):
    values = {"status": case.expected_final_status.value}
    if case.case_kind == "bug":
        values.update(
            affected_endpoint=case.endpoint,
            root_cause="PRIVATE_BASELINE_MARKER",
            suggested_fix="PRIVATE_BASELINE_MARKER",
            evidence=[
                {"source": "api_execution", "finding": "PRIVATE_BASELINE_MARKER"}
            ],
        )
    elif case.case_kind == "no_bug":
        values["evidence"] = [
            {"source": "api_execution", "finding": "PRIVATE_BASELINE_MARKER"}
        ]
    else:
        values["missing_evidence"] = ["PRIVATE_BASELINE_MARKER"]
    return FinalAnswerDecision(kind="final_answer", diagnosis=DebugDiagnosis(**values))


class ProbeProvider:
    async def next_decision(self, state):
        if not state.observations:
            return ToolCallDecision(
                kind="tool_call",
                tool_name="execute_api_request",
                arguments={"method": "GET", "path": "/orders/1"},
            )
        return final(CASES[state.issue])


class Judge:
    model = "mock-judge"

    def __init__(self):
        self.calls = 0

    async def judge(self, request, response_type):
        self.calls += 1
        labels = {
            "root_cause": "CORRECT",
            "grounding": "GROUNDED",
            "response_quality": "GOOD",
            "tool_selection": "APPROPRIATE",
        }
        return response_type(
            label=labels[request.evaluator],
            explanation="PRIVATE_BASELINE_MARKER",
            evidence_refs=["user_report"] if request.evaluator == "grounding" else [],
        )


@pytest.fixture
def experiment(tmp_path):
    directory = tmp_path / "baseline"
    initialize(directory, CONFIG, mode="offline")
    return directory


@pytest.mark.asyncio
async def test_full_dataset_isolated_setup_and_safe_complete_report(
    experiment, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    paths = []
    original = baseline.Database

    class TrackingDatabase(original):
        def __init__(self, path):
            paths.append(path)
            super().__init__(path)

    monkeypatch.setattr(baseline, "Database", TrackingDatabase)
    exporter = InMemorySpanExporter()
    provider = configure_tracing(span_exporter=exporter)
    try:
        await collect_runs(experiment, ProbeProvider, tracer=get_tracer(provider))
    finally:
        shutdown_tracing(provider)
    assert len(paths) == len(set(paths)) == 18
    assert not any(
        path.exists() for path in paths
    )  # Temporary SQLite files cleaned up.
    snapshots = [load_snapshot(experiment, case.case_id) for case in cases()]
    assert len({run.session_id for run in snapshots}) == 18
    assert len({run.trace_id for run in snapshots}) == 18
    for case, run in zip(cases(), snapshots, strict=True):
        assert run.comparison.model == CONFIG.agent_model
        assert run.comparison.limits == CONFIG.limits
        assert run.comparison.fixture_id == case.fixture_id
        assert run.tool_attempts[0].span_id
        # Exactly the three schema variants have ordered create-order setup.
        assert run.observations[0].observation.data["status_code"] == (
            200 if case.setup_requests else 404
        )
    assert "PRIVATE_BASELINE_MARKER" not in str(
        [span.attributes for span in exporter.get_finished_spans()]
    )
    judge = Judge()
    await evaluate_saved(experiment, judge)
    assert judge.calls > 18
    summary = build_summary(experiment)
    assert summary["total_cases"] == 18
    assert summary["execution_states"] == {"RETURNED": 18}
    assert summary["expected_status"] == {
        "denominator": 18,
        "matches": 18,
        "mismatches": 0,
        "missing": 0,
    }
    assert summary["successful_runs"] == 18
    assert summary["evaluator_versions_observed"]["grounding"] == ["2"]
    assert all(
        record["convergence"]["ratio"] == 1
        for record in summary["cases"]
        if record["case_kind"] == "bug"
    )
    assert summary["measurements"]["model_calls"]["sum_observed"] is None
    assert summary["measurements"]["model_calls"]["missing_cases"] == 18
    assert "PRIVATE_BASELINE_MARKER" not in json.dumps(summary) + render_report(summary)
    report = save_report(experiment)
    assert (report / "summary.json").exists()
    assert "OFFLINE SMOKE" in (report / "report.md").read_text()
    if os.name == "posix":
        assert (report / "summary.json").stat().st_mode & 0o777 == 0o600
        assert experiment.stat().st_mode & 0o777 == 0o700


@pytest.mark.asyncio
async def test_resume_and_new_evaluation_never_reexecute_agent(experiment):
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        return ProbeProvider()

    await collect_runs(experiment, factory)
    originals = {
        case.case_id: (
            case_directory(experiment, case.case_id) / "run.json"
        ).read_bytes()
        for case in cases()
    }
    judge = Judge()
    await evaluate_saved(experiment, judge)
    judged = judge.calls
    await collect_runs(experiment, factory)
    await evaluate_saved(experiment, judge)
    assert calls == 18 and judge.calls == judged
    await evaluate_saved(experiment, judge, revision="reviewed")
    assert calls == 18 and judge.calls == 2 * judged
    assert all(
        (case_directory(experiment, key) / "run.json").read_bytes() == value
        for key, value in originals.items()
    )
    with pytest.raises(FileExistsError):
        initialize(experiment, CONFIG, mode="offline")


@pytest.mark.asyncio
async def test_cancellation_preserves_partial_snapshot_and_does_not_retry(experiment):
    entered = asyncio.Event()
    executions = []

    class Interruptible:
        async def next_decision(self, state):
            case = CASES[state.issue]
            executions.append(case.case_id)
            if len(executions) == 3:
                entered.set()
                await asyncio.Event().wait()
            return final(case)

    task = asyncio.create_task(collect_runs(experiment, Interruptible))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    interrupted_id = cases()[2].case_id
    snapshot = load_snapshot(experiment, interrupted_id)
    assert snapshot.diagnosis is None and len(snapshot.decisions) == 1
    await collect_runs(experiment, Interruptible)
    assert len(executions) == len(set(executions)) == 18
    assert load_snapshot(experiment, interrupted_id).session_id == snapshot.session_id
    await evaluate_saved(experiment, Judge())
    summary = build_summary(experiment)
    assert summary["execution_states"] == {"RETURNED": 17, "INTERRUPTED": 1}
    assert summary["expected_status"]["missing"] == 1
    assert summary["evaluators"]["grounding"]["states"]["UNDETERMINED"] == 1


@pytest.mark.asyncio
async def test_hard_interruption_marker_and_factory_failure_remain_accounted(
    experiment,
):
    first = cases()[0].case_id
    write_private(
        case_directory(experiment, first) / "started.json",
        {
            "started_at": "earlier",
            "experiment_id": load_manifest(experiment).experiment_id,
            "case_id": first,
        },
    )
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("PRIVATE_BASELINE_MARKER")
        return ProbeProvider()

    await collect_runs(experiment, factory)
    assert calls == 17
    await evaluate_saved(experiment, Judge())
    summary = build_summary(experiment)
    assert summary["execution_states"] == {
        "INTERRUPTED": 1,
        "EXCEPTION": 1,
        "RETURNED": 16,
    }
    assert summary["expected_status"]["missing"] == 2
    assert summary["evaluators"]["grounding"]["missing_units"] == 2
    assert summary["evaluators"]["tool_selection"]["unknown_unit_cases"] == 2
    assert summary["cases"][0]["evaluators"]["tool_selection"]["expected_units"] is None
    assert "PRIVATE_BASELINE_MARKER" not in json.dumps(summary)


@pytest.mark.asyncio
async def test_judge_failures_and_execution_exceptions_are_separate(
    experiment, monkeypatch
):
    class Provider:
        async def next_decision(self, state):
            if state.issue == cases()[0].user_report:
                raise RuntimeError("PRIVATE_BASELINE_MARKER")
            return final(CASES[state.issue])

    class FailingJudge(Judge):
        async def judge(self, request, response_type):
            if request.evaluator == "grounding":
                raise JudgeError("JUDGE_API_ERROR")
            return await super().judge(request, response_type)

    await collect_runs(experiment, Provider)
    await evaluate_saved(experiment, FailingJudge())
    summary = build_summary(experiment)
    assert summary["execution_states"] == {"EXCEPTION": 1, "RETURNED": 17}
    assert summary["evaluation_states"] == {"COMPLETE": 1, "EVALUATOR_ERRORS": 17}
    assert summary["evaluators"]["grounding"]["states"]["ERROR"] == 17
    assert summary["evaluators"]["grounding"]["states"]["UNDETERMINED"] == 1
    assert summary["successful_runs"] == 0
    assert "PRIVATE_BASELINE_MARKER" not in json.dumps(summary)


@pytest.mark.asyncio
async def test_evaluation_interruption_resume_does_not_repeat_judge(
    experiment, monkeypatch
):
    await collect_runs(experiment, ProbeProvider)
    original = baseline.evaluate_run
    count = 0

    async def evaluate(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise asyncio.CancelledError
        return await original(*args)

    monkeypatch.setattr(baseline, "evaluate_run", evaluate)
    judge = Judge()
    with pytest.raises(asyncio.CancelledError):
        await evaluate_saved(experiment, judge)
    await evaluate_saved(experiment, judge)
    assert count == 18
    summary = build_summary(experiment)
    assert summary["evaluation_states"] == {"COMPLETE": 17, "ERROR": 1}
    assert summary["evaluators"]["root_cause"]["missing_units"] == 1


@pytest.mark.asyncio
async def test_setup_failure_remains_a_case_without_model_execution(
    experiment, monkeypatch
):
    original = baseline.Database
    called = 0

    class FailedSetup(original):
        def initialize(self):
            nonlocal called
            called += 1
            if called == 1:
                raise OSError("PRIVATE_BASELINE_MARKER")
            super().initialize()

    monkeypatch.setattr(baseline, "Database", FailedSetup)
    await collect_runs(experiment, ProbeProvider)
    summary = build_summary(experiment)
    assert summary["execution_states"] == {"SETUP_ERROR": 1, "RETURNED": 17}
    assert summary["evaluation_states"] == {"NO_SNAPSHOT": 1, "NOT_EVALUATED": 17}
    assert summary["measurements"]["latency_seconds"]["missing_cases"] == 1
    assert summary["measurements"]["latency_seconds"]["total_all_cases"] is None


def test_freeze_and_calibration_mix_prevention(experiment):
    manifest = load_manifest(experiment)
    path = experiment / "manifest.json"
    data = manifest.model_dump(mode="json")
    data["dataset_sha256"] = "different"
    path.write_text(json.dumps(data))
    with pytest.raises(BaselineError, match="DATASET_MISMATCH"):
        load_manifest(experiment)
    data["dataset_sha256"] = manifest.dataset_sha256
    data["source_sha256"] = "different"
    path.write_text(json.dumps(data))
    with pytest.raises(BaselineError, match="SOURCE_MISMATCH"):
        load_manifest(experiment)
    data["source_sha256"] = manifest.source_sha256
    data["runtime_versions"]["python"] = "0"
    path.write_text(json.dumps(data))
    with pytest.raises(BaselineError, match="RUNTIME_MISMATCH"):
        load_manifest(experiment)
    with pytest.raises(FileExistsError):
        write_private(path, {})


@pytest.mark.asyncio
async def test_snapshot_tamper_and_wrong_judge_are_rejected(experiment):
    await collect_runs(experiment, ProbeProvider)
    with pytest.raises(BaselineError, match="JUDGE_MODEL_MISMATCH"):
        await evaluate_saved(experiment, OfflineJudge())
    path = case_directory(experiment, cases()[0].case_id) / "run.json"
    data = json.loads(path.read_text())
    data["session_id"] = "different-session"
    path.write_text(json.dumps(data))
    with pytest.raises(BaselineError, match="SNAPSHOT_CHECKSUM_MISMATCH"):
        await collect_runs(experiment, ProbeProvider)


@pytest.mark.asyncio
async def test_actual_sdk_usage_excludes_judge_requests(experiment):
    requests = []

    def handler(request):
        requests.append(request)
        diagnosis = DebugDiagnosis(
            status="INCONCLUSIVE", missing_evidence=["more information"]
        )
        return httpx.Response(
            200,
            json={
                "id": f"resp_{len(requests)}",
                "object": "response",
                "created_at": 0,
                "model": "mock-agent",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "m",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": diagnosis.model_dump_json(),
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                    "input_tokens_details": {"cached_tokens": 2},
                    "output_tokens_details": {"reasoning_tokens": 1},
                },
            },
        )

    async with AsyncOpenAI(
        api_key="mock",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as client:
        await collect_runs(
            experiment,
            lambda: OpenAIDecisionProvider(
                client=client, model="mock-agent", reasoning_effort="medium"
            ),
        )
    judge = Judge()
    await evaluate_saved(experiment, judge)
    summary = build_summary(experiment)
    assert len(requests) == 18 and judge.calls == 36
    assert summary["measurements"]["model_calls"]["total_all_cases"] == 18
    assert summary["measurements"]["total_tokens"]["total_all_cases"] == 270
    assert summary["expected_status"] == {
        "denominator": 18,
        "matches": 1,
        "mismatches": 17,
        "missing": 0,
    }
    assert Counter(record["actual_final_status"] for record in summary["cases"]) == {
        "INCONCLUSIVE": 18
    }


@pytest.mark.asyncio
async def test_session_deadlines_remain_limit_reached_outcomes(experiment, monkeypatch):
    from agent_service.agent import loop

    original_timeout = asyncio.timeout

    def fast_clock(seconds):
        assert seconds == CONFIG.limits.session_timeout_seconds
        return original_timeout(0.001)

    monkeypatch.setattr(
        loop,
        "asyncio",
        SimpleNamespace(
            timeout=fast_clock,
            CancelledError=asyncio.CancelledError,
        ),
    )

    class Waiting:
        async def next_decision(self, state):
            await asyncio.Event().wait()

    await collect_runs(experiment, Waiting)
    await evaluate_saved(experiment, Judge())
    summary = build_summary(experiment)
    assert summary["execution_states"] == {"RETURNED": 18}
    assert summary["final_statuses"] == {"LIMIT_REACHED": 18}
    assert summary["expected_status"]["mismatches"] == 18
    assert all(
        record["system_metrics"]["error_counts"] == {"SESSION_TIMEOUT": 1}
        for record in summary["cases"]
    )


@pytest.mark.asyncio
async def test_unexpected_evaluator_exception_is_saved_without_message(
    experiment, monkeypatch
):
    await collect_runs(experiment, ProbeProvider)
    original = baseline.evaluate_run
    calls = 0

    async def evaluate(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("PRIVATE_BASELINE_MARKER")
        return await original(*args)

    monkeypatch.setattr(baseline, "evaluate_run", evaluate)
    await evaluate_saved(experiment, Judge())
    await evaluate_saved(experiment, Judge())
    assert calls == 18
    summary = build_summary(experiment)
    assert summary["evaluation_states"] == {"ERROR": 1, "COMPLETE": 17}
    assert summary["evaluators"]["root_cause"]["missing_units"] == 1
    assert "PRIVATE_BASELINE_MARKER" not in json.dumps(summary)


@pytest.mark.asyncio
async def test_foreign_case_directory_cannot_be_resumed_or_reported(
    experiment, tmp_path
):
    other = tmp_path / "other-experiment"
    initialize(
        other, CONFIG.model_copy(update={"agent_model": "other-agent"}), mode="offline"
    )
    await collect_runs(other, ProbeProvider)
    case_id = cases()[0].case_id
    shutil.copytree(case_directory(other, case_id), case_directory(experiment, case_id))
    with pytest.raises(BaselineError, match="CASE_EXPERIMENT_MISMATCH"):
        await collect_runs(experiment, ProbeProvider)
    with pytest.raises(BaselineError, match="CASE_EXPERIMENT_MISMATCH"):
        build_summary(experiment)
    # Even relabeling the start marker cannot hide foreign model/source context.
    start = case_directory(experiment, case_id) / "started.json"
    value = json.loads(start.read_text())
    value["experiment_id"] = load_manifest(experiment).experiment_id
    start.write_text(json.dumps(value))
    with pytest.raises(BaselineError, match="SNAPSHOT_CONFIGURATION_MISMATCH"):
        load_snapshot(experiment, case_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("dataset_version", "foreign-dataset"),
        ("agent_version", "foreign-source"),
        ("fixture_id", "foreign-fixture"),
        ("model", "foreign-model"),
        ("model_settings", {"reasoning_effort": "different"}),
        (
            "limits",
            {
                "max_agent_decisions": 1,
                "max_tool_calls": 6,
                "max_identical_tool_calls": 2,
                "session_timeout_seconds": 45,
            },
        ),
    ],
)
async def test_snapshot_configuration_must_match_manifest(experiment, field, value):
    await collect_runs(experiment, ProbeProvider)
    case_id = cases()[0].case_id
    path = case_directory(experiment, case_id) / "run.json"
    data = json.loads(path.read_text())
    data["comparison"][field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(BaselineError, match="SNAPSHOT_CONFIGURATION_MISMATCH"):
        load_snapshot(experiment, case_id)


@pytest.mark.asyncio
async def test_missing_committed_snapshot_and_wrong_execution_identity_rejected(
    experiment,
):
    await collect_runs(experiment, ProbeProvider)
    first, second = cases()[:2]
    (case_directory(experiment, first.case_id) / "run.json").unlink()
    with pytest.raises(BaselineError, match="MISSING_COMMITTED_SNAPSHOT"):
        await collect_runs(experiment, ProbeProvider)
    path = case_directory(experiment, second.case_id) / "execution.json"
    data = json.loads(path.read_text())
    data["case_id"] = first.case_id
    path.write_text(json.dumps(data))
    with pytest.raises(BaselineError, match="EXECUTION_CASE_MISMATCH"):
        load_snapshot(experiment, second.case_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["experiment_id", "judge_model", "source_sha256"])
async def test_foreign_evaluation_metadata_is_rejected(experiment, field):
    await collect_runs(experiment, ProbeProvider)
    await evaluate_saved(experiment, Judge())
    path = experiment / "evaluations" / "initial" / "metadata.json"
    data = json.loads(path.read_text())
    data[field] = "foreign"
    path.write_text(json.dumps(data))
    with pytest.raises(BaselineError, match="EVALUATION_METADATA_MISMATCH"):
        build_summary(experiment)


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong", ["judge", "metrics", "result_session"])
async def test_evaluation_results_must_match_frozen_judge_and_snapshot(
    experiment, wrong
):
    await collect_runs(experiment, ProbeProvider)
    await evaluate_saved(experiment, Judge())
    path = experiment / "evaluations" / "initial" / cases()[0].case_id / "result.json"
    data = json.loads(path.read_text())
    if wrong == "judge":
        data["results"][0]["judge_model"] = "other-judge"
    elif wrong == "result_session":
        data["results"][0]["session_id"] = "other-session"
    else:
        data["system_metrics"]["model_calls"] = 999
    path.write_text(json.dumps(data))
    with pytest.raises(BaselineError, match="EVALUATION_CONFIGURATION_MISMATCH"):
        await evaluate_saved(experiment, Judge())
