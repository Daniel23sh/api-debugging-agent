"""Explicit paid-run gate and verified local Phoenix links for the fixed baseline."""

import argparse
import asyncio
import json
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from urllib.parse import quote, urlencode, urlsplit
from uuid import uuid4

import httpx
from openai import AsyncOpenAI

from agent_service.agent.openai_provider import OpenAIDecisionProvider
from agent_service.observability import (
    configure_openai_instrumentation,
    configure_tracing,
    get_tracer,
    shutdown_openai_instrumentation,
    shutdown_tracing,
)
from evaluation.baseline import (
    BASELINES,
    EXPERIMENT_KINDS,
    ROOT,
    BaselineConfig,
    BaselineError,
    _fixture,
    cases,
    collect_runs,
    digest,
    evaluate_saved,
    initialize,
    load_manifest,
    new_manifest,
    utc_now,
    write_private,
)
from evaluation.baseline_report import build_summary, save_report
from evaluation.compare import reference_check, validate_reference
from evaluation.judges import OpenAIJudgeProvider
from sandbox_api.db import Database
from sandbox_api.main import create_app


def phoenix_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise BaselineError("PHOENIX_ORIGIN_REQUIRED_WITHOUT_CREDENTIALS")
    return value.rstrip("/")


@contextmanager
def phoenix_environment(settings: dict):
    values = {
        "PHOENIX_COLLECTOR_ENDPOINT": settings["url"],
        "PHOENIX_PROJECT_NAME": settings["project_name"],
        "OTEL_SERVICE_NAME": settings["service_name"],
    }
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def check_requests() -> list[dict]:
    """No model calls: exercise the fixed setup/reproduction requests in isolation."""
    records = []
    with TemporaryDirectory(prefix="apilens-preflight-") as temporary:
        for case in cases():
            path = Path(temporary) / f"{case.case_id}.db"
            database = Database(path)
            database.initialize()
            database.reset()
            _fixture(database)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(path)),
                base_url="http://sandbox",
            ) as client:
                for request in case.setup_requests:
                    response = await client.request(
                        request.method.value, request.path, json=request.body
                    )
                    if response.status_code >= 400:
                        raise BaselineError("PREFLIGHT_SETUP_FAILED")
                request = case.reproduction_request
                status = None
                if request:
                    response = await client.request(
                        request.method.value, request.path, json=request.body
                    )
                    status = response.status_code
                    if status != case.actual_status:
                        raise BaselineError("PREFLIGHT_REPRODUCTION_FAILED")
            records.append(
                {
                    "case_id": case.case_id,
                    "setup_requests": len(case.setup_requests),
                    "method": request.method.value if request else None,
                    "path": request.path if request else None,
                    "actual_status": status,
                }
            )
    return records


async def phoenix_span(
    client: httpx.AsyncClient,
    settings: dict,
    *,
    trace_id: str,
    span_id: str,
    name: str,
) -> dict:
    """Verify the exact root span, not an HTTP-200 SPA fallback or another trace."""
    project = quote(settings["project_name"], safe="")
    query = urlencode({"trace_id": trace_id, "span_id": span_id, "limit": 1})
    url = f"{settings['url']}/v1/projects/{project}/spans?{query}"
    for attempt in range(10):
        response = await client.get(url)
        if response.status_code == 200:
            spans = response.json().get("data", [])
            if any(
                span.get("context") == {"trace_id": trace_id, "span_id": span_id}
                and span.get("name") == name
                and span.get("parent_id") is None
                for span in spans
            ):
                return {"state": "VERIFIED", "trace_id": trace_id, "url": url}
        elif response.status_code != 404:
            raise BaselineError("PHOENIX_QUERY_FAILED")
        if attempt < 9:
            await asyncio.sleep(0.5)
    return {"state": "NOT_FOUND", "trace_id": trace_id, "url": None}


def check_openai_environment() -> None:
    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise BaselineError("OPENAI_API_KEY_REQUIRED")
    if os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") != (
        "https://api.openai.com/v1"
    ):
        raise BaselineError("UNAPPROVED_OPENAI_BASE_URL")


async def preflight(
    directory: Path,
    config: BaselineConfig,
    *,
    url: str,
    variant: Literal["v1", "v2", "v3"] = "v1",
    v1_directory: Path | None = None,
    v1_report_id: str | None = None,
    v1_source_ref: str | None = None,
) -> None:
    url = phoenix_url(url)
    check_openai_environment()
    binding = None
    if variant == "v2":
        if v1_directory is None or v1_report_id is None or v1_source_ref is None:
            raise BaselineError("V1_REFERENCE_REQUIRED")
        candidate = new_manifest(config, mode="live", variant="v2")
        comparison = reference_check(
            v1_directory, v1_report_id, v1_source_ref, candidate.model_dump(mode="json")
        )
        if not comparison["comparable"]:
            raise BaselineError("COMPARISON_RUNTIME_MISMATCH")
        binding = {
            "v1_experiment_id": comparison["experiment_ids"]["v1"],
            "v1_directory": str(v1_directory.resolve()),
            "v1_report_id": v1_report_id,
            "v1_report_sha256": digest(
                v1_directory / "reports" / v1_report_id / "summary.json"
            ),
            "v1_source_ref": v1_source_ref,
        }
    requests = await check_requests()
    async with httpx.AsyncClient(timeout=10) as client:
        health = await client.get(f"{url}/healthz")
        if health.status_code != 200 or health.text.strip() != "OK":
            raise BaselineError("PHOENIX_UNAVAILABLE")
        manifest = initialize(directory, config, mode="live", variant=variant)
        if binding is not None:
            write_private(
                directory / "comparison.json",
                {
                    **binding,
                    "v2_experiment_id": manifest.experiment_id,
                },
            )
        settings = {
            "experiment_id": manifest.experiment_id,
            "url": url,
            "project_name": f"apilens-{manifest.variant}-{manifest.experiment_id}",
            "service_name": os.getenv("OTEL_SERVICE_NAME", "apilens-agent-service"),
        }
        with phoenix_environment(settings):
            provider = configure_tracing()
            try:
                with get_tracer(provider).start_as_current_span(
                    "baseline_preflight"
                ) as span:
                    context = span.get_span_context()
                    trace_id, span_id = (
                        f"{context.trace_id:032x}",
                        f"{context.span_id:016x}",
                    )
                    span.set_attribute("baseline.experiment_id", manifest.experiment_id)
            finally:
                shutdown_tracing(provider)
        verified = await phoenix_span(
            client,
            settings,
            trace_id=trace_id,
            span_id=span_id,
            name="baseline_preflight",
        )
        if verified["state"] != "VERIFIED":
            raise BaselineError("PHOENIX_EXPORT_NOT_VERIFIED")
        write_private(directory / "phoenix.json", settings)
        write_private(
            directory / "preflight.json",
            {
                "experiment_id": manifest.experiment_id,
                "source_sha256": manifest.source_sha256,
                "checked_at": utc_now(),
                "requests": requests,
                "phoenix_probe": {**verified, "span_id": span_id},
                "paid_calls_made": 0,
                **(
                    {"comparison_sha256": digest(directory / "comparison.json")}
                    if binding
                    else {}
                ),
            },
        )


def live_settings(directory: Path) -> dict:
    manifest = load_manifest(directory)
    settings = json.loads((directory / "phoenix.json").read_text())
    check = json.loads((directory / "preflight.json").read_text())
    if (
        manifest.mode != "live"
        or settings["experiment_id"] != manifest.experiment_id
        or check["experiment_id"] != manifest.experiment_id
        or check["source_sha256"] != manifest.source_sha256
        or settings["project_name"]
        != f"apilens-{manifest.variant}-{manifest.experiment_id}"
        or len(check["requests"]) != 18
        or check["phoenix_probe"]["state"] != "VERIFIED"
    ):
        raise BaselineError("LIVE_PREFLIGHT_MISMATCH")
    phoenix_url(settings["url"])
    if manifest.variant == "v2":
        validate_reference(directory, manifest.model_dump(mode="json"))
    return settings


async def verify_traces(directory: Path) -> Path:
    settings = live_settings(directory)
    summary = build_summary(directory)
    records = []
    async with httpx.AsyncClient(timeout=10) as client:
        for case in summary["cases"]:
            if not case["trace_id"] or not case["session_span_id"]:
                result = {"state": "NO_TRACE", "trace_id": None, "url": None}
            else:
                result = await phoenix_span(
                    client,
                    settings,
                    trace_id=case["trace_id"],
                    span_id=case["session_span_id"],
                    name="DebugSession",
                )
            records.append({"case_id": case["case_id"], **result})
    destination = directory / "trace-checks" / f"{uuid4()}.json"
    write_private(
        destination,
        {
            "experiment_id": settings["experiment_id"],
            "checked_at": utc_now(),
            "cases": records,
        },
    )
    if any(record["state"] != "VERIFIED" for record in records):
        raise BaselineError("PHOENIX_TRACE_VERIFICATION_INCOMPLETE")
    return destination


async def run_live(directory: Path, *, approve_paid: bool) -> None:
    if not approve_paid:
        raise BaselineError("EXPLICIT_PAID_APPROVAL_REQUIRED")
    check_openai_environment()
    settings = live_settings(directory)
    manifest = load_manifest(directory)
    # Recheck ingestion before constructing any paid provider, including on resume.
    probe = json.loads((directory / "preflight.json").read_text())["phoenix_probe"]
    async with httpx.AsyncClient(timeout=10) as client:
        result = await phoenix_span(
            client,
            settings,
            trace_id=probe["trace_id"],
            span_id=probe["span_id"],
            name="baseline_preflight",
        )
        if result["state"] != "VERIFIED":
            raise BaselineError("PHOENIX_PREFLIGHT_TRACE_UNAVAILABLE")
    with phoenix_environment(settings):
        provider = configure_tracing()
        instrumentor = configure_openai_instrumentation(provider)
        try:
            async with AsyncOpenAI(max_retries=0) as client:
                await collect_runs(
                    directory,
                    lambda: OpenAIDecisionProvider(
                        client=client,
                        model=manifest.config.agent_model,
                        reasoning_effort=manifest.config.reasoning_effort,
                    ),
                    tracer=get_tracer(provider),
                )
                async with OpenAIJudgeProvider(
                    model=manifest.config.judge_model, client=client
                ) as judge:
                    await evaluate_saved(directory, judge)
        finally:
            shutdown_openai_instrumentation(instrumentor)
            shutdown_tracing(provider)
    save_report(directory)
    await verify_traces(directory)


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "run", "verify-traces"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--judge-model")
    parser.add_argument("--phoenix-url", default="http://127.0.0.1:6006")
    parser.add_argument("--approve-paid", action="store_true")
    parser.add_argument("--variant", choices=EXPERIMENT_KINDS, default="v1")
    parser.add_argument("--v1-directory", type=Path)
    parser.add_argument("--v1-report-id")
    parser.add_argument("--v1-source-ref")
    args = parser.parse_args()
    directory = args.directory.resolve()
    if not directory.is_relative_to(BASELINES) or directory == BASELINES:
        parser.error("use a dedicated directory under evaluation/artifacts/baseline/")
    try:
        if args.action == "preflight":
            if not args.judge_model:
                raise BaselineError("EXPLICIT_JUDGE_MODEL_REQUIRED")
            config = BaselineConfig(
                agent_model=os.getenv("OPENAI_MODEL", "gpt-6-sol"),
                reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT", "medium"),
                judge_model=args.judge_model,
            )
            asyncio.run(
                preflight(
                    directory,
                    config,
                    url=args.phoenix_url,
                    variant=args.variant,
                    v1_directory=args.v1_directory,
                    v1_report_id=args.v1_report_id,
                    v1_source_ref=args.v1_source_ref,
                )
            )
        elif args.action == "run":
            asyncio.run(run_live(directory, approve_paid=args.approve_paid))
        else:
            asyncio.run(verify_traces(directory))
    except Exception as error:  # noqa: BLE001 -- provider/HTTP bodies stay private
        code = str(error) if isinstance(error, BaselineError) else type(error).__name__
        parser.exit(1, f"Baseline operation failed: {code}\n")
    print(f"{args.action} completed: {directory.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
