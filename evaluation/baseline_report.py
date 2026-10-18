"""Reports contain counts and identifiers, never raw reports/evidence/judge prose."""

import json
import os
from collections import Counter
from pathlib import Path
from uuid import uuid4

from evaluation.baseline import (
    CaseExecution,
    case_directory,
    cases,
    evaluation_directory,
    load_bundle,
    load_manifest,
    load_snapshot,
    write_private,
)
from evaluation.contracts import EvaluatorName, ResultState
from evaluation.metrics import collect_system_metrics
from evaluation.trajectory import assess_success, convergence


def _measurement(values: list[int | float | None]) -> dict:
    observed = [value for value in values if value is not None]
    return {
        "observed_cases": len(observed),
        "missing_cases": len(values) - len(observed),
        "sum_observed": sum(observed) if observed else None,
        "mean_observed": sum(observed) / len(observed) if observed else None,
        "total_all_cases": sum(observed) if len(observed) == len(values) else None,
    }


def build_summary(directory: Path, *, revision: str = "initial") -> dict:
    manifest = load_manifest(directory)
    records = []
    runs, results_by_session = [], {}
    evaluator_totals = {
        name.value: {
            "expected_units": 0,
            "missing_units": 0,
            "unknown_unit_cases": 0,
            "states": {state.value: 0 for state in ResultState},
            "labels": {},
        }
        for name in EvaluatorName
    }
    measurements = {
        key: []
        for key in (
            "latency_seconds",
            "model_calls",
            "tool_calls",
            "input_tokens",
            "output_tokens",
            "total_tokens",
        )
    }
    versions: dict[str, set[str]] = {name.value: set() for name in EvaluatorName}
    for case in cases():
        location = case_directory(directory, case.case_id)
        run = load_snapshot(directory, case.case_id)
        bundle = load_bundle(directory, case.case_id, revision=revision)
        execution_path = location / "execution.json"
        execution = (
            CaseExecution.model_validate_json(execution_path.read_text())
            if execution_path.exists()
            else None
        )
        execution_state = (
            execution.state
            if execution
            else "INTERRUPTED"
            if (location / "started.json").exists()
            else "NOT_STARTED"
        )
        error_path = (
            evaluation_directory(directory, revision) / case.case_id / "error.json"
        )
        evaluation_error = (
            json.loads(error_path.read_text())["error_type"]
            if error_path.exists()
            else None
        )
        metrics = collect_system_metrics(run) if run else None
        expected_status_match = (
            run.diagnosis.status == case.expected_final_status
            if run and run.diagnosis
            else None
        )
        actual_results = bundle.results if bundle else []
        evaluation_state = (
            "COMPLETE"
            if bundle
            else "ERROR"
            if evaluation_error
            else "NO_SNAPSHOT"
            if run is None
            else "NOT_EVALUATED"
        )
        if bundle and any(
            result.state is ResultState.ERROR for result in actual_results
        ):
            evaluation_state = "EVALUATOR_ERRORS"
        eligibility = assess_success(case, run, actual_results) if run else None
        record = {
            "case_id": case.case_id,
            "case_kind": case.case_kind.value,
            "execution_state": execution_state,
            "execution_error_type": execution.error_type if execution else None,
            "evaluation_state": evaluation_state,
            "evaluation_error_type": evaluation_error,
            "session_id": run.session_id if run else None,
            "trace_id": run.trace_id
            if run
            else execution.trace_id
            if execution
            else None,
            "session_span_id": run.session_span_id
            if run
            else execution.session_span_id
            if execution
            else None,
            "expected_final_status": case.expected_final_status.value,
            "actual_final_status": run.diagnosis.status.value
            if run and run.diagnosis
            else None,
            "expected_status_match": expected_status_match,
            "success_eligibility": eligibility.model_dump(mode="json")
            if eligibility
            else None,
            "system_metrics": metrics.model_dump(mode="json") if metrics else None,
            "decision_attempts": len(run.decisions) if run else None,
            "path_length": len(run.decisions) + len(run.tool_attempts) if run else None,
            "evaluators": {},
        }
        # Error messages/observations are private. The safe report retains types only.
        if record["system_metrics"] is not None:
            record["system_metrics"].pop("errors")
        for name in EvaluatorName:
            unit_results = [
                result for result in actual_results if result.evaluator is name
            ]
            decision_unit = name in {
                EvaluatorName.TOOL_SELECTION,
                EvaluatorName.TOOL_ARGUMENTS,
            }
            expected_units = (
                len(run.decisions)
                if run and decision_unit
                else None
                if decision_unit
                else 1
            )
            totals = evaluator_totals[name.value]
            totals["expected_units"] += expected_units or 0
            totals["missing_units"] += (
                expected_units - len(unit_results) if expected_units is not None else 0
            )
            totals["unknown_unit_cases"] += expected_units is None
            states = Counter(result.state.value for result in unit_results)
            labels = Counter(
                result.label.value
                for result in unit_results
                if result.label is not None
            )
            for state, count in states.items():
                totals["states"][state] += count
            for label, count in labels.items():
                totals["labels"][label] = totals["labels"].get(label, 0) + count
            versions[name.value].update(result.version for result in unit_results)
            record["evaluators"][name.value] = {
                "expected_units": expected_units,
                "missing_units": expected_units - len(unit_results)
                if expected_units is not None
                else None,
                "states": dict(states),
                "labels": dict(labels),
                "values": [
                    {key: value for key, value in result.value.items() if key != "path"}
                    for result in unit_results
                    if result.value is not None
                ],
            }
        for key, measurements_for_key in measurements.items():
            value = getattr(metrics, key, None) if metrics else None
            if key.endswith("tokens"):
                value = (
                    getattr(metrics.token_usage, key, None)
                    if metrics and metrics.token_usage
                    else None
                )
            measurements_for_key.append(value)
        if run:
            runs.append(run)
            results_by_session[run.session_id] = actual_results
        records.append(record)
    ratios = convergence(
        {case.case_id: case for case in cases()}, runs, results_by_session
    )
    ratios_by_session = {
        result.session_id: result.model_dump(mode="json") for result in ratios
    }
    for record in records:
        record["convergence"] = ratios_by_session.get(record["session_id"])
    return {
        "kind": "apilens-v1-baseline-summary",
        "experiment": manifest.model_dump(mode="json"),
        "evaluation_revision": revision,
        "total_cases": len(records),
        "execution_states": dict(
            Counter(record["execution_state"] for record in records)
        ),
        "evaluation_states": dict(
            Counter(record["evaluation_state"] for record in records)
        ),
        "final_statuses": dict(
            Counter(record["actual_final_status"] or "MISSING" for record in records)
        ),
        "expected_status": {
            "denominator": len(records),
            "matches": sum(
                record["expected_status_match"] is True for record in records
            ),
            "mismatches": sum(
                record["expected_status_match"] is False for record in records
            ),
            "missing": sum(
                record["expected_status_match"] is None for record in records
            ),
        },
        "successful_runs": sum(
            bool(
                record["success_eligibility"]
                and record["success_eligibility"]["eligible"]
            )
            for record in records
        ),
        "evaluator_versions_observed": {
            name: sorted(value) for name, value in versions.items()
        },
        "evaluators": evaluator_totals,
        "measurements": {
            name: _measurement(value) for name, value in measurements.items()
        },
        "cases": records,
        "limitations": [
            "Offline mode is plumbing verification, not V1 performance data.",
            "Label distributions use scored units; states and missing units are reported separately.",
            "Observed sums/means are partial when missing_cases is nonzero; total_all_cases is then null.",
            "Convergence uses only successful comparable observed paths and does not prove optimality.",
            "Trace IDs are references only; Phoenix availability must be independently verified.",
            "Judges are qualitative measurements, not ground truth; no aggregate quality score is computed.",
        ],
    }


def render_report(summary: dict) -> str:
    manifest = summary["experiment"]
    title = (
        "V1 baseline"
        if manifest["mode"] == "live"
        else "OFFLINE SMOKE — not V1 performance results"
    )
    lines = [
        f"# {title}",
        "",
        f"Experiment: `{manifest['experiment_id']}`; evaluation: `{summary['evaluation_revision']}`.",
        f"Agent: `{manifest['config']['agent_model']}` / `{manifest['config']['reasoning_effort']}`; judge: `{manifest['config']['judge_model']}`.",
        f"Source: `{manifest['git_commit']}` / `{manifest['source_sha256']}`.",
        f"Dataset: `{manifest['dataset_sha256']}`; fixture: `{manifest['fixture_id']}`.",
        "",
        "All 18 cases are included. Calibration completion is not agent task success.",
        "",
        "## Aggregate measurements",
        "",
        "```json",
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "total_cases",
                    "execution_states",
                    "evaluation_states",
                    "final_statuses",
                    "expected_status",
                    "successful_runs",
                    "evaluator_versions_observed",
                    "evaluators",
                    "measurements",
                )
            },
            indent=2,
        ),
        "```",
        "",
        "## Per-case results",
        "",
        "| Case | Execution | Expected / actual | Status match | Evaluation | Success eligibility | Root / grounding / quality | Trace ID |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for record in summary["cases"]:
        verdicts = []
        for name in ("root_cause", "grounding", "response_quality"):
            entry = record["evaluators"][name]
            verdicts.append(
                json.dumps(entry["labels"] or entry["states"])
                if not entry["missing_units"]
                else "MISSING"
            )
        lines.append(
            f"| {record['case_id']} | {record['execution_state']} | {record['expected_final_status']} / {record['actual_final_status'] or 'MISSING'} | {record['expected_status_match']} | {record['evaluation_state']} | {(record['success_eligibility'] or {}).get('reason', 'NO_SNAPSHOT')} | {' / '.join(verdicts)} | {record['trace_id'] or 'MISSING'} |"
        )
    lines += [
        "",
        "## Limitations",
        "",
        *[f"- {value}" for value in summary["limitations"]],
    ]
    return "\n".join(lines) + "\n"


def save_report(directory: Path, *, revision: str = "initial") -> Path:
    summary = build_summary(directory, revision=revision)
    report = directory / "reports" / str(uuid4())
    write_private(report / "summary.json", summary)
    descriptor = os.open(
        report / "report.md", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    with os.fdopen(descriptor, "w") as output:
        output.write(render_report(summary))
    return report
