"""Six-case human calibration only; this is not a baseline/experiment runner."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import httpx
from openai import AsyncOpenAI
from opentelemetry.trace import Tracer
from pydantic import Field, TypeAdapter

from agent_service.agent.openai_provider import OpenAIDecisionProvider
from agent_service.main import ProviderFactory, run_debug_session
from agent_service.observability import (
    configure_openai_instrumentation,
    configure_tracing,
    get_tracer,
    shutdown_openai_instrumentation,
    shutdown_tracing,
)
from evaluation.contracts import (
    ComparisonContext,
    Contract,
    EvaluationRun,
    GroundingLabel,
    QualityLabel,
    RootCauseLabel,
)
from evaluation.dataset import CaseKind, EvaluationCase, load_evaluation_cases
from evaluation.recorder import EvaluationRecorder
from sandbox_api.db import Database
from sandbox_api.main import create_app

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation" / "datasets" / "v1_cases.json"
ARTIFACTS = ROOT / "evaluation" / "artifacts"
CALIBRATION_CASE_IDS = (
    "inventory_validation_zero_01",
    "order_total_quantity_02",
    "order_missing_inventory_03",
    "order_response_schema_02",
    "order_create_status_no_bug",
    "insufficient_report_no_endpoint",
)


class HumanAnnotation(Contract):
    case_id: str
    root_cause: RootCauseLabel | Literal["NOT_APPLICABLE"]
    grounding: GroundingLabel
    response_quality: QualityLabel
    notes: str = Field(default="", max_length=2000)


class DisagreementResolution(Contract):
    case_id: str
    evaluator: Literal["root_cause", "grounding", "response_quality"]
    category: Literal["human_label", "rubric", "judge_error", "isolated_disagreement"]
    resolution: str = Field(min_length=1, max_length=2000)


class HumanReview(Contract):
    resolutions: list[DisagreementResolution] = Field(default_factory=list)
    no_material_unresolved_pattern: bool = False
    comment: str = Field(default="", max_length=2000)


def calibration_cases() -> list[EvaluationCase]:
    cases = {case.case_id: case for case in load_evaluation_cases(DATASET)}
    return [cases[case_id] for case_id in CALIBRATION_CASE_IDS]


def _save(path: Path, value: object) -> None:
    # Artifacts may contain private reports/evidence; never overwrite human work.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)


def _source_version() -> str:
    digest = hashlib.sha256()
    for directory in ("agent_service", "sandbox_api"):
        for source in sorted((ROOT / directory).rglob("*.py")):
            digest.update(str(source.relative_to(ROOT)).encode())
            digest.update(source.read_bytes())
    return digest.hexdigest()


async def collect_calibration(
    directory: Path,
    provider_factory: ProviderFactory,
    *,
    model: str,
    model_settings: dict[str, str] | None = None,
    tracer: Tracer | None = None,
) -> list[EvaluationRun]:
    """Capture exactly six sequential runs; setup belongs to the fixture, not agent evidence."""
    directory.mkdir(parents=True, exist_ok=False)
    runs: list[EvaluationRun] = []
    cases = calibration_cases()
    with TemporaryDirectory(prefix="apilens-calibration-") as temporary:
        path = Path(temporary) / "sandbox.db"
        database = Database(path)
        database.initialize()
        transport = httpx.ASGITransport(app=create_app(path))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://sandbox"
        ) as client:
            for case in cases:
                database.reset()
                if (
                    database.get_inventory(1).quantity != 10
                    or database.get_inventory(2).quantity != 5
                    or database.get_inventory(3).quantity != 0
                    or database.get_order(1) is not None
                ):
                    raise RuntimeError("Calibration fixture reset failed")
                for setup in case.setup_requests:
                    kwargs = {"json": setup.body} if setup.body is not None else {}
                    response = await client.request(
                        setup.method.value, setup.path, **kwargs
                    )
                    if response.status_code >= 400:
                        raise RuntimeError("Calibration setup failed")
                recorder = EvaluationRecorder(
                    case.case_id,
                    comparison=ComparisonContext(
                        dataset_version=hashlib.sha256(
                            DATASET.read_bytes()
                        ).hexdigest(),
                        fixture_id=case.fixture_id,
                        agent_version=_source_version(),
                        model=model,
                        model_settings=model_settings or {},
                    ),
                )
                await run_debug_session(
                    case.user_report,
                    provider_factory,
                    transport=transport,
                    database=database,
                    tracer=tracer,
                    recorder=recorder,
                )
                snapshot = recorder.snapshot()
                runs.append(snapshot)
                _save(
                    directory / f"{case.case_id}.run.json",
                    snapshot.model_dump(mode="json"),
                )
    _save(
        directory / "human-review.json",
        [
            {
                "case_id": case.case_id,
                "case_kind": case.case_kind.value,
                "user_report": case.user_report,
                "expected_root_cause": case.expected_root_cause,
                "expected_final_status": case.expected_final_status.value,
                "diagnosis": run.diagnosis.model_dump(mode="json")
                if run.diagnosis
                else None,
                "observations": [
                    item.model_dump(mode="json") for item in run.observations
                ],
            }
            for case, run in zip(cases, runs, strict=True)
        ],
    )
    _save(
        directory / "human-labels.json",
        [
            {
                "case_id": case.case_id,
                "root_cause": None,
                "grounding": None,
                "response_quality": None,
                "notes": "",
            }
            for case in cases
        ],
    )
    return runs


def load_calibration_runs(directory: Path) -> list[EvaluationRun]:
    runs = [
        EvaluationRun.model_validate_json(
            (directory / f"{case_id}.run.json").read_text(encoding="utf-8")
        )
        for case_id in CALIBRATION_CASE_IDS
    ]
    if [run.case_id for run in runs] != list(CALIBRATION_CASE_IDS):
        raise ValueError("calibration snapshot case IDs do not match")
    return runs


def compare_labels(directory: Path, results: dict[str, dict]) -> dict:
    """Reveal judge labels only after all six genuine human annotations are supplied."""
    annotations = TypeAdapter(list[HumanAnnotation]).validate_json(
        (directory / "human-labels.json").read_text(encoding="utf-8")
    )
    if len(annotations) != 6 or {item.case_id for item in annotations} != set(
        CALIBRATION_CASE_IDS
    ):
        raise ValueError("exactly the six calibration cases must be human-labeled")
    comparisons = []
    cases = {case.case_id: case for case in calibration_cases()}
    for human in annotations:
        if cases[human.case_id].case_kind is CaseKind.BUG:
            if human.root_cause == "NOT_APPLICABLE":
                raise ValueError("bug calibration cases require a root-cause label")
        elif human.root_cause not in {"NOT_APPLICABLE", RootCauseLabel.INCORRECT}:
            raise ValueError(
                "non-bug root cause must be not applicable or a false-bug error"
            )
        for evaluator in ("root_cause", "grounding", "response_quality"):
            result = results[human.case_id][evaluator]
            judge_label = (
                result["label"] if result["state"] == "SCORED" else result["state"]
            )
            human_label = getattr(human, evaluator)
            comparable = result["state"] in ("SCORED", "NOT_APPLICABLE")
            comparisons.append(
                {
                    "case_id": human.case_id,
                    "evaluator": evaluator,
                    "human": human_label,
                    "judge": judge_label,
                    "agreement": human_label == judge_label if comparable else None,
                    "human_notes": human.notes,
                }
            )
    return {"comparisons": comparisons, "calibration_complete": False}


def finalize_review(comparison: dict, review: HumanReview) -> dict:
    unresolved = {
        (item["case_id"], item["evaluator"])
        for item in comparison["comparisons"]
        if item["agreement"] is not True
    }
    resolved = {(item.case_id, item.evaluator) for item in review.resolutions}
    if len(resolved) != len(review.resolutions) or resolved != unresolved:
        raise ValueError(
            "each disagreement or unavailable judgment needs exactly one resolution"
        )
    if not review.no_material_unresolved_pattern or not review.comment.strip():
        raise ValueError(
            "human confirmation about remaining disagreement patterns is required"
        )
    if any(item["agreement"] is None for item in comparison["comparisons"]):
        raise ValueError(
            "unavailable judge results must be evaluated again before calibration closes"
        )
    return {
        **comparison,
        "review": review.model_dump(mode="json"),
        "calibration_complete": True,
    }


async def _judge_saved(directory: Path, output: Path) -> None:
    from evaluation.judges import OpenAIJudgeProvider
    from evaluation.orchestration import evaluate_run

    runs = load_calibration_runs(directory)
    results = {}
    async with OpenAIJudgeProvider() as judge:
        for case, run in zip(calibration_cases(), runs, strict=True):
            bundle = await evaluate_run(case, run, judge)
            results[case.case_id] = bundle.model_dump(mode="json")
    _save(output, results)


async def _collect_live(directory: Path) -> None:
    from evaluation.judges import OpenAIJudgeProvider

    # Fail before any paid agent run if an explicit judge model is missing.
    judge = OpenAIJudgeProvider()
    await judge.aclose()
    provider = configure_tracing()
    instrumentor = None
    try:
        instrumentor = configure_openai_instrumentation(provider)
        async with AsyncOpenAI(max_retries=0) as client:

            def factory():
                return OpenAIDecisionProvider(client=client)

            await collect_calibration(
                directory,
                factory,
                model=OpenAIDecisionProvider(client=client).model,
                model_settings={
                    "reasoning_effort": OpenAIDecisionProvider(
                        client=client
                    ).reasoning_effort
                },
                tracer=get_tracer(provider),
            )
        await _judge_saved(directory, directory / "judge-results.json")
    finally:
        shutdown_openai_instrumentation(instrumentor)
        shutdown_tracing(provider)


async def _rejudge_live(directory: Path, output: Path) -> None:
    provider = configure_tracing()
    instrumentor = None
    try:
        instrumentor = configure_openai_instrumentation(provider)
        await _judge_saved(directory, output)
    finally:
        shutdown_openai_instrumentation(instrumentor)
        shutdown_tracing(provider)


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("collect", "rejudge", "compare", "finalize"))
    parser.add_argument(
        "directory", type=Path, help="local directory under evaluation/artifacts"
    )
    parser.add_argument(
        "--results", default="judge-results.json", help="saved judge result filename"
    )
    args = parser.parse_args()
    directory = args.directory.resolve()
    if not directory.is_relative_to(ARTIFACTS):
        parser.error(
            "calibration artifacts must remain in ignored evaluation/artifacts/"
        )
    if Path(args.results).name != args.results:
        parser.error("--results must be a filename")
    if args.action == "collect":
        asyncio.run(_collect_live(directory))
    elif args.action == "rejudge":
        asyncio.run(_rejudge_live(directory, directory / args.results))
    else:
        bundles = json.loads((directory / args.results).read_text(encoding="utf-8"))
        results = {
            case_id: {item["evaluator"]: item for item in bundle["results"]}
            for case_id, bundle in bundles.items()
        }
        comparison = compare_labels(directory, results)
        if args.action == "finalize":
            review = HumanReview.model_validate_json(
                (directory / "review.json").read_text(encoding="utf-8")
            )
            comparison = finalize_review(comparison, review)
        print(json.dumps(comparison, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
