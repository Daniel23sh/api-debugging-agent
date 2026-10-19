"""Archived reports retain frozen measurements without enabling execution."""

import asyncio
import json
import sys
from math import inf, nextafter

import pytest

from evaluation import baseline, baseline_report


@pytest.fixture
def archived_experiment(tmp_path):
    directory = tmp_path / "baseline"
    baseline.initialize(
        directory,
        baseline.BaselineConfig(
            agent_model="offline-agent",
            reasoning_effort="offline",
            judge_model="offline-judge",
        ),
        mode="offline",
    )
    asyncio.run(baseline.collect_runs(directory, baseline.OfflineProvider))
    asyncio.run(baseline.evaluate_saved(directory, baseline.OfflineJudge()))
    report = baseline_report.save_report(directory)
    return directory, report, json.loads((report / "summary.json").read_text())


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["source", "runtime", "limits"])
async def test_archived_report_survives_execution_default_changes(
    archived_experiment, monkeypatch, capsys, drift
):
    directory, report, recorded = archived_experiment
    before = {
        path: path.read_bytes() for path in directory.rglob("*") if path.is_file()
    }
    if drift == "source":
        monkeypatch.setattr(baseline, "source_digest", lambda: "repaired-source")
        error = "SOURCE_MISMATCH"
    elif drift == "runtime":
        monkeypatch.setattr(baseline, "runtime_versions", lambda: {"python": "future"})
        error = "RUNTIME_MISMATCH"
    else:
        changed_limits = baseline.AgentLimits().model_copy(
            update={"max_agent_decisions": 99}
        )
        monkeypatch.setattr(baseline, "AgentLimits", lambda: changed_limits)
        error = "LIMITS_MISMATCH"

    def no_recomputation(*args, **kwargs):
        pytest.fail("historical reads must not recompute evaluator/metric semantics")

    monkeypatch.setattr(baseline, "evaluate_run", no_recomputation)
    monkeypatch.setattr(baseline, "collect_system_metrics", no_recomputation)
    monkeypatch.setattr(baseline_report, "assess_success", no_recomputation)
    monkeypatch.setattr(baseline_report, "convergence", no_recomputation)
    assert baseline.load_archived_report(directory, report.name) == recorded
    monkeypatch.setattr(baseline, "BASELINES", directory.parent)
    monkeypatch.setattr(
        sys, "argv", ["baseline", "archive", str(directory), "--report-id", report.name]
    )
    baseline.main()
    assert json.loads(capsys.readouterr().out) == recorded
    with pytest.raises(baseline.BaselineError, match=error):
        baseline.load_manifest(directory)
    with pytest.raises(baseline.BaselineError, match=error):
        await baseline.collect_runs(directory, baseline.OfflineProvider)
    with pytest.raises(baseline.BaselineError, match=error):
        await baseline.evaluate_saved(
            directory, baseline.OfflineJudge(), revision="new"
        )
    with pytest.raises(baseline.BaselineError, match=error):
        baseline.load_snapshot(directory, baseline.cases()[0].case_id)
    with pytest.raises(baseline.BaselineError, match=error):
        baseline.load_bundle(directory, baseline.cases()[0].case_id, revision="initial")
    with pytest.raises(baseline.BaselineError, match=error):
        baseline_report.save_report(directory)
    assert {
        path: path.read_bytes() for path in directory.rglob("*") if path.is_file()
    } == before


@pytest.mark.parametrize(
    "artifact,field,value,error",
    [
        ("manifest", "dataset_sha256", "foreign", "DATASET_MISMATCH"),
        ("started", "experiment_id", "foreign", "CASE_EXPERIMENT_MISMATCH"),
        ("summary_trace", "trace_id", "foreign", "ARCHIVED_REPORT_MISMATCH"),
        ("summary_span", "session_span_id", "foreign", "ARCHIVED_REPORT_MISMATCH"),
        ("result_trace", "trace_id", "foreign", "EVALUATION_RUN_MISMATCH"),
        ("result_version", "version", "future", "ARCHIVED_REPORT_MISMATCH"),
        ("summary_experiment", "experiment_id", "foreign", "ARCHIVED_REPORT_MISMATCH"),
        ("summary_versions", "grounding", ["future"], "ARCHIVED_REPORT_MISMATCH"),
        ("summary_labels", "labels", {"GROUNDED": 1}, "ARCHIVED_REPORT_MISMATCH"),
        ("run_context", "model", "foreign", "SNAPSHOT_CONFIGURATION_MISMATCH"),
        ("run_limits", "max_agent_decisions", 99, "SNAPSHOT_CONFIGURATION_MISMATCH"),
        ("execution", "snapshot_sha256", "foreign", "SNAPSHOT_CHECKSUM_MISMATCH"),
        ("metadata", "judge_model", "foreign", "EVALUATION_METADATA_MISMATCH"),
        ("result", "judge_model", "foreign", "EVALUATION_CONFIGURATION_MISMATCH"),
    ],
)
def test_archived_report_retains_artifact_binding(
    archived_experiment, monkeypatch, artifact, field, value, error
):
    directory, report, _ = archived_experiment
    case_id = baseline.cases()[0].case_id
    paths = {
        "manifest": directory / "manifest.json",
        "started": directory / "cases" / case_id / "started.json",
        "summary_trace": report / "summary.json",
        "summary_span": report / "summary.json",
        "result_trace": directory / "evaluations" / "initial" / case_id / "result.json",
        "result_version": directory
        / "evaluations"
        / "initial"
        / case_id
        / "result.json",
        "summary_experiment": report / "summary.json",
        "summary_versions": report / "summary.json",
        "summary_labels": report / "summary.json",
        "run_context": directory / "cases" / case_id / "run.json",
        "run_limits": directory / "cases" / case_id / "run.json",
        "execution": directory / "cases" / case_id / "execution.json",
        "metadata": directory / "evaluations" / "initial" / "metadata.json",
        "result": directory / "evaluations" / "initial" / case_id / "result.json",
    }
    path = paths[artifact]
    data = json.loads(path.read_text())
    target = {
        "summary_experiment": lambda: data["experiment"],
        "summary_trace": lambda: data["cases"][0],
        "summary_span": lambda: data["cases"][0],
        "result_version": lambda: data["results"][0],
        "summary_versions": lambda: data["evaluator_versions_observed"],
        "summary_labels": lambda: data["cases"][0]["evaluators"]["grounding"],
        "run_context": lambda: data["comparison"],
        "run_limits": lambda: data["comparison"]["limits"],
        "result": lambda: data["results"][0],
    }.get(artifact, lambda: data)()
    target[field] = value
    path.write_text(json.dumps(data))
    monkeypatch.setattr(baseline, "source_digest", lambda: "repaired-source")
    with pytest.raises(baseline.BaselineError, match=error):
        baseline.load_archived_report(directory, report.name)


def test_archived_report_rejects_path_traversal(archived_experiment):
    directory, _, _ = archived_experiment
    with pytest.raises(baseline.BaselineError, match="INVALID_REPORT_ID"):
        baseline.load_archived_report(directory, "../manifest.json")


@pytest.mark.parametrize(
    "path,value",
    [
        (("successful_runs",), 18),
        (("expected_status", "denominator"), 17),
        (("expected_status", "matches"), 18),
        (("expected_status", "mismatches"), 0),
        (("expected_status", "missing"), 1),
        (("execution_states",), {"RETURNED": 17}),
        (("evaluation_states",), {"COMPLETE": 17}),
        (("final_statuses",), {"RESOLVED": 18}),
        (("measurements", "latency_seconds", "sum_observed"), 1_000_000),
        (("measurements", "tool_calls", "total_all_cases"), 0),
        (("measurements", "model_calls", "observed_cases"), 18),
        (("measurements", "input_tokens", "sum_observed"), 0),
        (("measurements", "input_tokens", "missing_cases"), 0),
        (("evaluators", "grounding", "labels"), {"GROUNDED": 18}),
        (("evaluators", "grounding", "states", "SCORED"), 17),
        (("evaluators", "tool_selection", "expected_units"), 0),
        (("evaluators", "tool_selection", "missing_units"), 1),
        (("evaluators", "tool_selection", "unknown_unit_cases"), 1),
    ],
)
def test_archived_report_rejects_aggregate_case_contradictions(
    archived_experiment, path, value
):
    directory, report, _ = archived_experiment
    filename = report / "summary.json"
    summary = json.loads(filename.read_text())
    target = summary
    for key in path[:-1]:
        target = target[key]
    assert target[path[-1]] != value
    target[path[-1]] = value
    filename.write_text(json.dumps(summary))
    with pytest.raises(baseline.BaselineError, match="ARCHIVED_REPORT_MISMATCH"):
        baseline.load_archived_report(directory, report.name)


def test_archived_measurements_allow_only_float_roundoff(
    archived_experiment, monkeypatch
):
    directory, report, recorded = archived_experiment
    filename = report / "summary.json"
    for statistic in ("sum_observed", "mean_observed", "total_all_cases"):
        value = recorded["measurements"]["latency_seconds"][statistic]
        recorded["measurements"]["latency_seconds"][statistic] = nextafter(value, inf)
    filename.write_text(json.dumps(recorded))
    before = filename.read_bytes()

    def no_reassessment(*args, **kwargs):
        pytest.fail("archive consistency must use saved case data only")

    monkeypatch.setattr(baseline_report, "collect_system_metrics", no_reassessment)
    monkeypatch.setattr(baseline_report, "assess_success", no_reassessment)
    monkeypatch.setattr(baseline_report, "convergence", no_reassessment)
    monkeypatch.setattr(baseline, "evaluate_run", no_reassessment)
    assert baseline.load_archived_report(directory, report.name) == recorded
    assert filename.read_bytes() == before


def test_record_rollups_preserve_zero_and_partial_usage(archived_experiment):
    _, _, recorded = archived_experiment
    rows = recorded["cases"][:3]
    rows[0]["system_metrics"] = {
        "latency_seconds": 0.0,
        "model_calls": 0,
        "tool_calls": 0,
        "token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    }
    rows[1]["system_metrics"] = {
        "latency_seconds": 0.2,
        "model_calls": 1,
        "tool_calls": 2,
        "token_usage": {"input_tokens": 3, "output_tokens": None, "total_tokens": None},
    }
    rows[2]["system_metrics"] = None
    rollups = baseline_report.aggregate_records(rows)
    assert rollups["total_cases"] == 3
    assert rollups["execution_states"] == {"RETURNED": 3}
    assert rollups["measurements"]["latency_seconds"] == {
        "observed_cases": 2,
        "missing_cases": 1,
        "sum_observed": 0.2,
        "mean_observed": 0.1,
        "total_all_cases": None,
    }
    assert rollups["measurements"]["input_tokens"]["mean_observed"] == 1.5
    assert rollups["measurements"]["output_tokens"] == {
        "observed_cases": 1,
        "missing_cases": 2,
        "sum_observed": 0,
        "mean_observed": 0.0,
        "total_all_cases": None,
    }


@pytest.mark.parametrize("later_tamper", [None, "run", "metadata", "judge"])
def test_archived_pending_reports_survive_later_collection_and_evaluation(
    tmp_path, later_tamper
):
    directory = tmp_path / "baseline"
    baseline.initialize(
        directory,
        baseline.BaselineConfig(
            agent_model="offline-agent",
            reasoning_effort="offline",
            judge_model="offline-judge",
        ),
        mode="offline",
    )
    before_collection = baseline_report.save_report(directory)
    case_id = baseline.cases()[0].case_id
    baseline.write_private(
        directory / "cases" / case_id / "started.json",
        {
            "experiment_id": baseline.load_manifest(directory).experiment_id,
            "case_id": case_id,
        },
    )
    before_interruption_recorded = baseline_report.save_report(directory)
    asyncio.run(baseline.collect_runs(directory, baseline.OfflineProvider))
    before_evaluation = baseline_report.save_report(directory)
    recorded = {
        report.name: json.loads((report / "summary.json").read_text())
        for report in (
            before_collection,
            before_interruption_recorded,
            before_evaluation,
        )
    }
    asyncio.run(baseline.evaluate_saved(directory, baseline.OfflineJudge()))
    artifacts = {
        path: path.read_bytes() for path in directory.rglob("*") if path.is_file()
    }
    for report_id, summary in recorded.items():
        assert baseline.load_archived_report(directory, report_id) == summary
    assert {
        path: path.read_bytes() for path in directory.rglob("*") if path.is_file()
    } == artifacts
    if later_tamper is not None:
        case_id = baseline.cases()[1].case_id
        if later_tamper == "run":
            path = directory / "cases" / case_id / "run.json"
            data = json.loads(path.read_text())
            data["comparison"]["model"] = "foreign"
            error = "SNAPSHOT_CONFIGURATION_MISMATCH"
        elif later_tamper == "metadata":
            path = directory / "evaluations" / "initial" / "metadata.json"
            data = json.loads(path.read_text())
            data["judge_model"] = "foreign"
            error = "EVALUATION_METADATA_MISMATCH"
        else:
            path = directory / "evaluations" / "initial" / case_id / "result.json"
            data = json.loads(path.read_text())
            data["results"][0]["judge_model"] = "foreign"
            error = "EVALUATION_CONFIGURATION_MISMATCH"
        path.write_text(json.dumps(data))
        with pytest.raises(baseline.BaselineError, match=error):
            baseline.load_archived_report(directory, before_collection.name)


def test_completed_archive_requires_recorded_bundle(archived_experiment):
    directory, report, _ = archived_experiment
    case_id = baseline.cases()[0].case_id
    (directory / "evaluations" / "initial" / case_id / "result.json").unlink()
    with pytest.raises(baseline.BaselineError, match="ARCHIVED_REPORT_MISMATCH"):
        baseline.load_archived_report(directory, report.name)


@pytest.mark.parametrize(
    "field,value",
    [
        ("states", {"SCORED": 1}),
        ("labels", {"GROUNDED": 1}),
        ("values", [{"path_length": 1}]),
    ],
)
def test_pending_archive_rejects_unrecorded_judgments(tmp_path, field, value):
    directory = tmp_path / "baseline"
    baseline.initialize(
        directory,
        baseline.BaselineConfig(
            agent_model="offline-agent",
            reasoning_effort="offline",
            judge_model="offline-judge",
        ),
        mode="offline",
    )
    report = baseline_report.save_report(directory)
    path = report / "summary.json"
    summary = json.loads(path.read_text())
    summary["cases"][0]["evaluators"]["grounding"][field] = value
    path.write_text(json.dumps(summary))
    with pytest.raises(baseline.BaselineError, match="ARCHIVED_REPORT_MISMATCH"):
        baseline.load_archived_report(directory, report.name)
