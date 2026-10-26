"""Fixed V1 dataset execution and private, resumable local artifacts."""

import argparse
import asyncio
import hashlib
import json
import os
import platform
import subprocess
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.metadata import version
from math import isclose, isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Literal
from uuid import UUID, uuid4

import httpx
from opentelemetry.trace import Tracer
from pydantic import Field

from agent_service.agent.schemas import (
    DebugDiagnosis,
    FinalAnswerDecision,
    ToolCallDecision,
)
from agent_service.agent.state import AgentLimits
from agent_service.main import ProviderFactory, run_debug_session
from evaluation.contracts import (
    ComparisonContext,
    Contract,
    EvaluationRun,
    EvaluatorName,
)
from evaluation.dataset import EvaluationCase, load_evaluation_cases
from evaluation.judges import JudgeProvider
from evaluation.metrics import collect_system_metrics
from evaluation.orchestration import EvaluationBundle, evaluate_run
from evaluation.recorder import EvaluationRecorder, span_ids
from sandbox_api.db import Database
from sandbox_api.main import create_app

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation" / "datasets" / "v1_cases.json"
BASELINES = ROOT / "evaluation" / "artifacts" / "baseline"
EXPERIMENT_KINDS = {
    "v1": "apilens-v1-baseline",
    "v2": "apilens-v2-experiment",
    "v3": "apilens-v3-verification",
}


class BaselineError(Exception):
    """Only fixed, non-sensitive error codes cross the CLI boundary."""


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_digest() -> str:
    checksum = hashlib.sha256()
    for folder in ("agent_service", "sandbox_api", "evaluation"):
        for source in sorted((ROOT / folder).rglob("*.py")):
            if "artifacts" not in source.relative_to(ROOT / folder).parts:
                checksum.update(str(source.relative_to(ROOT)).encode())
                checksum.update(source.read_bytes())
    for name in ("pyproject.toml", "constraints-dev.txt"):
        checksum.update((ROOT / name).read_bytes())
    return checksum.hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def runtime_versions() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        **{
            name: version(name)
            for name in (
                "openai",
                "pydantic",
                "httpx",
                "fastapi",
                "opentelemetry-sdk",
                "arize-phoenix-otel",
            )
        },
    }


class BaselineConfig(Contract):
    agent_model: str = Field(min_length=1)
    reasoning_effort: str = Field(min_length=1)
    judge_model: str = Field(min_length=1)
    limits: AgentLimits = Field(default_factory=AgentLimits)


class BaselineManifest(Contract):
    kind: Literal[
        "apilens-v1-baseline", "apilens-v2-experiment", "apilens-v3-verification"
    ] = "apilens-v1-baseline"
    format_version: Literal[1] = 1
    experiment_id: str
    mode: Literal["offline", "live"]
    created_at: str
    config: BaselineConfig
    case_ids: list[str]
    fixture_id: Literal["sandbox-v1"] = "sandbox-v1"
    dataset_sha256: str
    source_sha256: str
    git_commit: str
    runtime_versions: dict[str, str]

    @property
    def variant(self) -> str:
        return next(name for name, kind in EXPERIMENT_KINDS.items() if kind == self.kind)


class CaseExecution(Contract):
    case_id: str
    state: Literal["RETURNED", "EXCEPTION", "SETUP_ERROR", "INTERRUPTED"]
    error_type: str | None = None
    snapshot_sha256: str | None = None
    trace_id: str | None = None
    session_span_id: str | None = None
    finished_at: str


def cases() -> list[EvaluationCase]:
    dataset = load_evaluation_cases(DATASET)
    if len(dataset) != 18 or {case.fixture_id for case in dataset} != {"sandbox-v1"}:
        raise BaselineError("UNEXPECTED_V1_DATASET")
    return dataset


def write_private(path: Path, value: object) -> None:
    """Atomically publish once; interruption never leaves a truncated final file."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".pending-", delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
        try:
            json.dump(value, temporary, ensure_ascii=False, indent=2, allow_nan=False)
            temporary.flush()
            os.fsync(temporary.fileno())
            os.link(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)


@contextmanager
def experiment_lock(directory: Path) -> Iterator[None]:
    lock = directory / ".lock"
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise BaselineError("EXPERIMENT_LOCKED") from None
    os.close(descriptor)
    try:
        yield
    finally:
        lock.unlink()


def new_manifest(
    config: BaselineConfig,
    *,
    mode: Literal["offline", "live"],
    variant: Literal["v1", "v2", "v3"] = "v1",
) -> BaselineManifest:
    dataset = cases()
    if config.limits != AgentLimits():
        raise BaselineError("V1_LIMITS_MUST_REMAIN_UNCHANGED")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return BaselineManifest(
        kind=EXPERIMENT_KINDS[variant],
        experiment_id=str(uuid4()),
        mode=mode,
        created_at=utc_now(),
        config=config,
        case_ids=[case.case_id for case in dataset],
        dataset_sha256=digest(DATASET),
        source_sha256=source_digest(),
        git_commit=commit,
        runtime_versions=runtime_versions(),
    )


def initialize(
    directory: Path,
    config: BaselineConfig,
    *,
    mode: Literal["offline", "live"],
    variant: Literal["v1", "v2", "v3"] = "v1",
) -> BaselineManifest:
    manifest = new_manifest(config, mode=mode, variant=variant)
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    write_private(directory / "manifest.json", manifest.model_dump(mode="json"))
    return manifest


def _read_manifest(directory: Path) -> BaselineManifest:
    manifest = BaselineManifest.model_validate_json(
        (directory / "manifest.json").read_text()
    )
    if manifest.dataset_sha256 != digest(DATASET) or manifest.case_ids != [
        case.case_id for case in cases()
    ]:
        raise BaselineError("DATASET_MISMATCH")
    return manifest


def load_manifest(directory: Path) -> BaselineManifest:
    manifest = _read_manifest(directory)
    if manifest.config.limits != AgentLimits():
        raise BaselineError("LIMITS_MISMATCH")
    if manifest.source_sha256 != source_digest():
        raise BaselineError("SOURCE_MISMATCH")
    if manifest.runtime_versions != runtime_versions():
        raise BaselineError("RUNTIME_MISMATCH")
    return manifest


def case_directory(directory: Path, case_id: str) -> Path:
    if case_id not in {case.case_id for case in cases()}:
        raise BaselineError("UNKNOWN_CASE")
    return directory / "cases" / case_id


def comparison_context(manifest: BaselineManifest) -> ComparisonContext:
    return ComparisonContext(
        dataset_version=manifest.dataset_sha256,
        fixture_id=manifest.fixture_id,
        agent_version=manifest.source_sha256,
        model=manifest.config.agent_model,
        model_settings={"reasoning_effort": manifest.config.reasoning_effort},
        limits=manifest.config.limits,
    )


def evaluation_metadata(manifest: BaselineManifest) -> dict:
    return {
        "experiment_id": manifest.experiment_id,
        "judge_model": manifest.config.judge_model,
        "source_sha256": manifest.source_sha256,
    }


def load_snapshot(directory: Path, case_id: str) -> EvaluationRun | None:
    return _read_snapshot(directory, case_id, load_manifest(directory))


def _read_snapshot(
    directory: Path, case_id: str, manifest: BaselineManifest
) -> EvaluationRun | None:
    location = case_directory(directory, case_id)
    path = location / "run.json"
    started_path, outcome = location / "started.json", location / "execution.json"
    execution = (
        CaseExecution.model_validate_json(outcome.read_text())
        if outcome.exists()
        else None
    )
    if started_path.exists():
        started = json.loads(started_path.read_text())
        if (
            started.get("experiment_id") != manifest.experiment_id
            or started.get("case_id") != case_id
        ):
            raise BaselineError("CASE_EXPERIMENT_MISMATCH")
    elif path.exists() or execution is not None:
        raise BaselineError("MISSING_CASE_START")
    if execution is not None and execution.case_id != case_id:
        raise BaselineError("EXECUTION_CASE_MISMATCH")
    if not path.exists():
        if execution is not None and (
            execution.snapshot_sha256 is not None or execution.state == "RETURNED"
        ):
            raise BaselineError("MISSING_COMMITTED_SNAPSHOT")
        return None
    run = EvaluationRun.model_validate_json(path.read_text())
    if run.case_id != case_id:
        raise BaselineError("SNAPSHOT_CASE_MISMATCH")
    if run.comparison != comparison_context(manifest) or any(
        call.model != manifest.config.agent_model for call in run.model_calls or []
    ):
        raise BaselineError("SNAPSHOT_CONFIGURATION_MISMATCH")
    if execution is not None:
        if execution.snapshot_sha256 != digest(path):
            raise BaselineError("SNAPSHOT_CHECKSUM_MISMATCH")
        if (
            execution.trace_id != run.trace_id
            or execution.session_span_id != run.session_span_id
        ):
            raise BaselineError("SNAPSHOT_TRACE_MISMATCH")
    return run


def _fixture(database: Database) -> None:
    if (
        database.get_product(1).name != "Mechanical Keyboard"
        or [database.get_inventory(i).quantity for i in (1, 2, 3)] != [10, 5, 0]
        or database.get_order(1) is not None
    ):
        raise BaselineError("FIXTURE_RESET_FAILED")


async def collect_runs(
    directory: Path, provider_factory: ProviderFactory, *, tracer: Tracer | None = None
) -> None:
    manifest = load_manifest(directory)
    with (
        experiment_lock(directory),
        TemporaryDirectory(prefix="apilens-baseline-") as temporary,
    ):
        for case in cases():
            location = case_directory(directory, case.case_id)
            if (location / "execution.json").exists():
                load_snapshot(directory, case.case_id)
                continue
            if (location / "started.json").exists():
                run = load_snapshot(directory, case.case_id)
                execution = CaseExecution(
                    case_id=case.case_id,
                    state="INTERRUPTED",
                    error_type="PREVIOUS_EXECUTION_INTERRUPTED",
                    finished_at=utc_now(),
                    snapshot_sha256=digest(location / "run.json") if run else None,
                    trace_id=run.trace_id if run else None,
                    session_span_id=run.session_span_id if run else None,
                )
                write_private(
                    location / "execution.json", execution.model_dump(mode="json")
                )
                continue
            write_private(
                location / "started.json",
                {
                    "experiment_id": manifest.experiment_id,
                    "case_id": case.case_id,
                    "started_at": utc_now(),
                },
            )
            recorder = EvaluationRecorder(
                case.case_id,
                comparison=comparison_context(manifest),
            )
            state = "SETUP_ERROR"
            error_type = None
            trace_id = session_span_id = None

            def provider():
                nonlocal trace_id, session_span_id
                trace_id, session_span_id = span_ids()
                selected = provider_factory()
                if (
                    getattr(selected, "model", manifest.config.agent_model)
                    != manifest.config.agent_model
                ):
                    raise BaselineError("AGENT_MODEL_MISMATCH")
                if (
                    getattr(
                        selected, "reasoning_effort", manifest.config.reasoning_effort
                    )
                    != manifest.config.reasoning_effort
                ):
                    raise BaselineError("AGENT_SETTINGS_MISMATCH")
                return selected

            interrupted = False
            try:
                database_path = Path(temporary) / f"{case.case_id}.db"
                database = Database(database_path)
                database.initialize()
                database.reset()
                _fixture(database)
                transport = httpx.ASGITransport(app=create_app(database_path))
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://sandbox"
                ) as client:
                    for setup in case.setup_requests:
                        kwargs = {"json": setup.body} if setup.body is not None else {}
                        response = await client.request(
                            setup.method.value, setup.path, **kwargs
                        )
                        if response.status_code >= 400:
                            raise BaselineError("SETUP_REQUEST_FAILED")
                state = "EXCEPTION"
                await run_debug_session(
                    case.user_report,
                    provider,
                    recorder=recorder,
                    transport=transport,
                    database=database,
                    tracer=tracer,
                )
                state = "RETURNED"
            except asyncio.CancelledError:
                state, error_type, interrupted = "INTERRUPTED", "CANCELLED", True
            except Exception as error:  # noqa: BLE001 -- preserve failed cases without private exception text
                error_type = type(error).__name__
            finally:
                try:
                    _ = recorder.run
                except ValueError:
                    snapshot = None  # Factory/setup failed before the agent started.
                else:
                    snapshot = recorder.snapshot()
                if snapshot is not None:
                    write_private(
                        location / "run.json", snapshot.model_dump(mode="json")
                    )
                    trace_id, session_span_id = (
                        snapshot.trace_id,
                        snapshot.session_span_id,
                    )
                execution = CaseExecution(
                    case_id=case.case_id,
                    state=state,
                    error_type=error_type,
                    trace_id=trace_id,
                    session_span_id=session_span_id,
                    snapshot_sha256=digest(location / "run.json") if snapshot else None,
                    finished_at=utc_now(),
                )
                write_private(
                    location / "execution.json", execution.model_dump(mode="json")
                )
            if interrupted:
                raise asyncio.CancelledError


def evaluation_directory(directory: Path, revision: str) -> Path:
    if not revision or not all(
        c.isascii() and (c.isalnum() or c in "-_") for c in revision
    ):
        raise BaselineError("INVALID_EVALUATION_REVISION")
    return directory / "evaluations" / revision


async def evaluate_saved(
    directory: Path, judge: JudgeProvider, *, revision: str = "initial"
) -> None:
    manifest = load_manifest(directory)
    target = evaluation_directory(directory, revision)
    with experiment_lock(directory):
        metadata = target / "metadata.json"
        expected = evaluation_metadata(manifest)
        if metadata.exists():
            if json.loads(metadata.read_text()) != expected:
                raise BaselineError("EVALUATION_METADATA_MISMATCH")
        else:
            write_private(metadata, expected)
        if getattr(judge, "model", None) != manifest.config.judge_model:
            raise BaselineError("JUDGE_MODEL_MISMATCH")
        for case in cases():
            location = target / case.case_id
            load_bundle(directory, case.case_id, revision=revision)
            if (location / "result.json").exists() or (
                location / "error.json"
            ).exists():
                continue
            if (location / "started.json").exists():
                write_private(
                    location / "error.json", {"error_type": "EVALUATION_INTERRUPTED"}
                )
                continue
            run = load_snapshot(directory, case.case_id)
            if run is None:
                continue
            write_private(
                location / "started.json",
                {
                    "session_id": run.session_id,
                    "snapshot_sha256": digest(
                        case_directory(directory, case.case_id) / "run.json"
                    ),
                },
            )
            try:
                result = await evaluate_run(case, run, judge)
            except asyncio.CancelledError:
                write_private(
                    location / "error.json", {"error_type": "EVALUATION_INTERRUPTED"}
                )
                raise
            except Exception as error:  # noqa: BLE001 -- evaluator failure is an explicit case outcome
                write_private(
                    location / "error.json", {"error_type": type(error).__name__}
                )
            else:
                write_private(location / "result.json", result.model_dump(mode="json"))


def load_bundle(
    directory: Path, case_id: str, *, revision: str
) -> EvaluationBundle | None:
    return _read_bundle(
        directory, case_id, revision, load_manifest(directory), check_metrics=True
    )


def _read_bundle(
    directory: Path,
    case_id: str,
    revision: str,
    manifest: BaselineManifest,
    *,
    check_metrics: bool,
) -> EvaluationBundle | None:
    target = evaluation_directory(directory, revision) / case_id
    path = target / "result.json"
    if not any(
        (target / name).exists()
        for name in ("result.json", "started.json", "error.json")
    ):
        return None
    metadata = json.loads((target.parent / "metadata.json").read_text())
    if metadata != evaluation_metadata(manifest):
        raise BaselineError("EVALUATION_METADATA_MISMATCH")
    run = _read_snapshot(directory, case_id, manifest)
    started = json.loads((target / "started.json").read_text())
    if (
        run is None
        or started["session_id"] != run.session_id
        or started["snapshot_sha256"]
        != digest(case_directory(directory, case_id) / "run.json")
    ):
        raise BaselineError("EVALUATION_RUN_MISMATCH")
    if not path.exists():
        return None
    bundle = EvaluationBundle.model_validate_json(path.read_text())
    if (
        bundle.case_id != case_id
        or bundle.session_id != run.session_id
        or bundle.trace_id != run.trace_id
    ):
        raise BaselineError("EVALUATION_RUN_MISMATCH")
    if (check_metrics and bundle.system_metrics != collect_system_metrics(run)) or any(
        result.case_id != run.case_id
        or result.session_id != run.session_id
        or result.trace_id != run.trace_id
        or (
            result.judge_model is not None
            and result.judge_model != manifest.config.judge_model
        )
        for result in bundle.results
    ):
        raise BaselineError("EVALUATION_CONFIGURATION_MISMATCH")
    return bundle


def load_archived_report(directory: Path, report_id: str) -> dict:
    """Read saved measurements under the current serialized schema, without evaluation."""
    try:
        UUID(report_id)
    except ValueError:
        raise BaselineError("INVALID_REPORT_ID") from None
    manifest = _read_manifest(directory)
    summary = json.loads(
        (directory / "reports" / report_id / "summary.json").read_text()
    )
    if (
        summary.get("kind") != f"{manifest.kind}-summary"
        or summary.get("experiment") != manifest.model_dump(mode="json")
        or summary.get("total_cases") != len(manifest.case_ids)
        or [record["case_id"] for record in summary["cases"]] != manifest.case_ids
    ):
        raise BaselineError("ARCHIVED_REPORT_MISMATCH")
    revision = summary["evaluation_revision"]
    evaluation_directory(directory, revision)
    versions: dict[str, set[str]] = {name.value: set() for name in EvaluatorName}
    for record in summary["cases"]:
        case_id = record["case_id"]
        run = _read_snapshot(directory, case_id, manifest)
        bundle = _read_bundle(
            directory, case_id, revision, manifest, check_metrics=False
        )
        if record["session_id"] is not None and (
            run is None
            or record["session_id"] != run.session_id
            or record["trace_id"] != run.trace_id
            or record["session_span_id"] != run.session_span_id
        ):
            raise BaselineError("ARCHIVED_REPORT_MISMATCH")
        # Later append-only work does not fill measurements absent in this report.
        if record["evaluation_state"] not in {"COMPLETE", "EVALUATOR_ERRORS"}:
            if any(
                record["evaluators"][name.value][key] != empty
                for name in EvaluatorName
                for key, empty in (("states", {}), ("labels", {}), ("values", []))
            ):
                raise BaselineError("ARCHIVED_REPORT_MISMATCH")
            continue
        if bundle is None:
            raise BaselineError("ARCHIVED_REPORT_MISMATCH")
        metrics = bundle.system_metrics.model_dump(mode="json", exclude={"errors"})
        if record["system_metrics"] != metrics:
            raise BaselineError("ARCHIVED_REPORT_MISMATCH")
        for name in EvaluatorName:
            results = [result for result in bundle.results if result.evaluator is name]
            recorded = record["evaluators"][name.value]
            expected = {
                "states": dict(Counter(result.state.value for result in results)),
                "labels": dict(
                    Counter(
                        result.label.value
                        for result in results
                        if result.label is not None
                    )
                ),
                "values": [
                    {key: value for key, value in result.value.items() if key != "path"}
                    for result in results
                    if result.value is not None
                ],
            }
            if any(recorded[key] != value for key, value in expected.items()):
                raise BaselineError("ARCHIVED_REPORT_MISMATCH")
            versions[name.value].update(result.version for result in results)
    if summary["evaluator_versions_observed"] != {
        name: sorted(value) for name, value in versions.items()
    }:
        raise BaselineError("ARCHIVED_REPORT_MISMATCH")
    from evaluation.baseline_report import aggregate_records

    # Validate saved arithmetic, not current eligibility or evaluator semantics.
    for field, expected in aggregate_records(summary["cases"]).items():
        saved = summary.get(field)
        if field != "measurements":
            if saved != expected:
                raise BaselineError("ARCHIVED_REPORT_MISMATCH")
            continue
        if not isinstance(saved, dict) or saved.keys() != expected.keys():
            raise BaselineError("ARCHIVED_REPORT_MISMATCH")
        for name, statistics in expected.items():
            recorded = saved[name]
            if not isinstance(recorded, dict) or recorded.keys() != statistics.keys():
                raise BaselineError("ARCHIVED_REPORT_MISMATCH")
            for statistic, value in statistics.items():
                actual = recorded[statistic]
                # Float summation can differ slightly across historical runtimes.
                if isinstance(value, float):
                    matches = (
                        type(actual) in (int, float)
                        and isfinite(actual)
                        and isclose(actual, value, rel_tol=1e-12, abs_tol=1e-12)
                    )
                else:
                    matches = actual == value
                if not matches:
                    raise BaselineError("ARCHIVED_REPORT_MISMATCH")
    return summary


class OfflineProvider:
    """Plumbing smoke test, deliberately not a V1 performance measurement."""

    async def next_decision(self, state):
        case = next(case for case in cases() if case.user_report == state.issue)
        if not state.observations and case.reproduction_request is not None:
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
        return FinalAnswerDecision(
            kind="final_answer",
            diagnosis=DebugDiagnosis(
                status="INCONCLUSIVE",
                missing_evidence=["Offline plumbing probe; not V1 output"],
            ),
        )


class OfflineJudge:
    model = "offline-judge"

    async def judge(self, request, response_type):
        labels = {
            "root_cause": "INCORRECT",
            "grounding": "UNGROUNDED",
            "response_quality": "POOR",
            "tool_selection": "APPROPRIATE",
        }
        return response_type(
            label=labels[request.evaluator],
            explanation="Offline plumbing probe",
            evidence_refs=["user_report"] if request.evaluator == "grounding" else [],
        )


def main() -> None:
    from evaluation.baseline_report import save_report

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("smoke", "summary", "archive"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--revision", default="initial")
    parser.add_argument("--variant", choices=EXPERIMENT_KINDS, default="v1")
    parser.add_argument(
        "--report-id", help="saved report UUID for read-only archive access"
    )
    args = parser.parse_args()
    directory = args.directory.resolve()
    if not directory.is_relative_to(BASELINES) or directory == BASELINES:
        parser.error("use a dedicated directory under evaluation/artifacts/baseline/")
    if args.action == "archive" and args.report_id is None:
        parser.error("archive requires --report-id")
    try:
        if args.action == "archive":
            print(json.dumps(load_archived_report(directory, args.report_id), indent=2))
            return
        if args.action == "smoke":
            if not directory.exists():
                initialize(
                    directory,
                    BaselineConfig(
                        agent_model="offline-agent",
                        reasoning_effort="offline",
                        judge_model="offline-judge",
                    ),
                    mode="offline",
                    variant=args.variant,
                )
            if load_manifest(directory).mode != "offline":
                raise BaselineError("CANNOT_SMOKE_TEST_A_LIVE_BASELINE")
            if load_manifest(directory).variant != args.variant:
                raise BaselineError("EXPERIMENT_VARIANT_MISMATCH")
            asyncio.run(collect_runs(directory, OfflineProvider))
            asyncio.run(
                evaluate_saved(directory, OfflineJudge(), revision=args.revision)
            )
        report = save_report(directory, revision=args.revision)
    except Exception as error:  # noqa: BLE001 -- never print private exception bodies
        code = str(error) if isinstance(error, BaselineError) else type(error).__name__
        parser.exit(1, f"Baseline operation failed: {code}\n")
    print(f"Private baseline report saved: {report.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
