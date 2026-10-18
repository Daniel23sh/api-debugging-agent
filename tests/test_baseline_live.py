import json

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent_service.observability import configure_tracing
from evaluation import baseline_live as live
from evaluation.baseline import (
    BaselineConfig,
    BaselineError,
    OfflineJudge,
    OfflineProvider,
    load_manifest,
)
from evaluation.baseline_report import build_summary

CONFIG = BaselineConfig(
    agent_model="gpt-6-sol", reasoning_effort="medium", judge_model="gpt-6.1-sol"
)


@pytest.fixture
def phoenix(monkeypatch):
    """Real exporter/span lifecycle, mocked Phoenix HTTP only; no OpenAI access."""

    class ServerStorage(InMemorySpanExporter):
        def shutdown(self):
            pass  # Mock server storage outlives each app-owned provider.

    exporter = ServerStorage()
    original_client = httpx.AsyncClient

    def respond(request):
        if request.url.path == "/healthz":
            return httpx.Response(200, text="OK")
        spans = [
            {
                "name": span.name,
                "parent_id": None if span.parent is None else "parent",
                "context": {
                    "trace_id": f"{span.context.trace_id:032x}",
                    "span_id": f"{span.context.span_id:016x}",
                },
                "attributes": {"private": "PRIVATE_PHOENIX_RESPONSE"},
            }
            for span in exporter.get_finished_spans()
            if f"{span.context.trace_id:032x}" == request.url.params.get("trace_id")
            and f"{span.context.span_id:016x}" == request.url.params.get("span_id")
        ]
        return httpx.Response(200, json={"data": spans})

    def client(**kwargs):
        if "transport" not in kwargs:
            kwargs["transport"] = httpx.MockTransport(respond)
        return original_client(**kwargs)

    monkeypatch.setattr(live.httpx, "AsyncClient", client)
    monkeypatch.setattr(
        live, "configure_tracing", lambda: configure_tracing(span_exporter=exporter)
    )
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE_TEST_KEY")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    return exporter


@pytest.mark.asyncio
async def test_preflight_covers_requests_and_verifies_export_without_openai(
    tmp_path, phoenix, monkeypatch
):
    monkeypatch.setattr(live, "AsyncOpenAI", lambda **_: pytest.fail("paid SDK"))
    directory = tmp_path / "live"
    await live.preflight(directory, CONFIG, url="http://phoenix")
    manifest = load_manifest(directory)
    settings = live.live_settings(directory)
    assert manifest.mode == "live" and manifest.config == CONFIG
    assert settings["project_name"] == f"apilens-v1-{manifest.experiment_id}"
    check = json.loads((directory / "preflight.json").read_text())
    assert check["paid_calls_made"] == 0
    assert len(check["requests"]) == 18
    assert sum(r["method"] is not None for r in check["requests"]) == 17
    assert sum(r["setup_requests"] for r in check["requests"]) == 3
    assert check["phoenix_probe"]["state"] == "VERIFIED"
    for path in directory.rglob("*.json"):
        assert "PRIVATE_" not in path.read_text()
    assert len(phoenix.get_finished_spans()) == 1
    span = phoenix.get_finished_spans()[0]
    assert (
        span.resource.attributes["openinference.project.name"]
        == settings["project_name"]
    )
    with pytest.raises(FileExistsError):
        await live.preflight(directory, CONFIG, url="http://phoenix")


@pytest.mark.asyncio
async def test_paid_gate_and_runtime_endpoint_change_stop_before_provider(
    tmp_path, phoenix, monkeypatch
):
    monkeypatch.setattr(live, "AsyncOpenAI", lambda **_: pytest.fail("paid SDK"))
    with pytest.raises(BaselineError, match="EXPLICIT_PAID_APPROVAL_REQUIRED"):
        await live.run_live(tmp_path / "absent", approve_paid=False)
    directory = tmp_path / "live"
    await live.preflight(directory, CONFIG, url="http://phoenix")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unapproved.example/v1")
    with pytest.raises(BaselineError, match="UNAPPROVED_OPENAI_BASE_URL"):
        await live.run_live(directory, approve_paid=True)


@pytest.mark.asyncio
async def test_missing_key_and_phoenix_do_not_create_live_experiment(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(BaselineError, match="OPENAI_API_KEY_REQUIRED"):
        await live.preflight(tmp_path / "absent", CONFIG, url="http://phoenix")
    assert not (tmp_path / "absent").exists()
    monkeypatch.setenv("OPENAI_API_KEY", "mock")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        live.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            **{
                **kwargs,
                "transport": kwargs.get("transport")
                or httpx.MockTransport(
                    lambda _: httpx.Response(200, text="<html>SPA fallback</html>")
                ),
            }
        ),
    )
    with pytest.raises(BaselineError, match="PHOENIX_UNAVAILABLE"):
        await live.preflight(tmp_path / "absent", CONFIG, url="http://phoenix")
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize(
    "url",
    [
        "https://key:secret@example.com",
        "http://host?secret=x",
        "http://host/#key",
        "file:///private",
        "http://host/v1/traces",
    ],
)
def test_phoenix_origin_disallows_secret_or_ambiguous_urls(url):
    with pytest.raises(BaselineError):
        live.phoenix_url(url)


@pytest.mark.asyncio
async def test_trace_verifier_rejects_wrong_identity_and_preserves_missing(
    tmp_path, phoenix, monkeypatch
):
    directory = tmp_path / "live"
    await live.preflight(directory, CONFIG, url="http://phoenix")
    path = await live.verify_traces(directory)
    assert {row["state"] for row in json.loads(path.read_text())["cases"]} == {
        "NO_TRACE"
    }

    async def no_sleep(_):
        pass

    monkeypatch.setattr(live.asyncio, "sleep", no_sleep)
    async with httpx.AsyncClient() as client:
        result = await live.phoenix_span(
            client,
            live.live_settings(directory),
            trace_id="0" * 32,
            span_id="0" * 16,
            name="DebugSession",
        )
    assert result == {"state": "NOT_FOUND", "trace_id": "0" * 32, "url": None}


@pytest.mark.asyncio
async def test_live_orchestration_frozen_providers_resume_and_verified_links(
    tmp_path, phoenix, monkeypatch
):
    directory = tmp_path / "live"
    await live.preflight(directory, CONFIG, url="http://phoenix")
    constructed = []

    class SDK:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class Judge(OfflineJudge):
        def __init__(self, *, model, client):
            assert isinstance(client, SDK)
            self.model = model

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    def provider(**kwargs):
        constructed.append(kwargs)
        assert kwargs["model"] == CONFIG.agent_model
        assert kwargs["reasoning_effort"] == CONFIG.reasoning_effort
        return OfflineProvider()

    monkeypatch.setattr(live, "AsyncOpenAI", lambda **_: SDK())
    monkeypatch.setattr(live, "OpenAIDecisionProvider", provider)
    monkeypatch.setattr(live, "OpenAIJudgeProvider", Judge)
    await live.run_live(directory, approve_paid=True)
    assert len(constructed) == 18
    summary = build_summary(directory)
    assert summary["total_cases"] == 18
    assert summary["execution_states"] == {"RETURNED": 18}
    paths = sorted((directory / "trace-checks").glob("*.json"))
    checked = json.loads(paths[0].read_text())
    assert len(checked["cases"]) == 18
    assert {row["state"] for row in checked["cases"]} == {"VERIFIED"}
    assert all("trace_id=" in row["url"] for row in checked["cases"])
    assert "PRIVATE_" not in paths[0].read_text()
    await live.run_live(directory, approve_paid=True)
    assert len(constructed) == 18
    assert len(list((directory / "trace-checks").glob("*.json"))) == 2
    assert len(list((directory / "reports").iterdir())) == 2


@pytest.mark.asyncio
async def test_frozen_live_metadata_cannot_be_borrowed_from_another_experiment(
    tmp_path, phoenix
):
    directory = tmp_path / "live"
    await live.preflight(directory, CONFIG, url="http://phoenix")
    path = directory / "phoenix.json"
    settings = json.loads(path.read_text())
    settings["experiment_id"] = "another-experiment"
    path.write_text(json.dumps(settings))
    with pytest.raises(BaselineError, match="LIVE_PREFLIGHT_MISMATCH"):
        live.live_settings(directory)
