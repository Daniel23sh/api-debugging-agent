"""Deterministic infrastructure checks; mock outcomes do not measure prompt quality."""

import ast
import asyncio
import copy
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent_service.agent import openai_provider
from agent_service.agent.schemas import DebugDiagnosis, FinalAnswerDecision
from agent_service.observability import configure_tracing
from evaluation import baseline, compare
from evaluation import baseline_live as live
from evaluation.baseline import BaselineConfig, BaselineError
from evaluation.baseline_report import render_report, save_report

CONFIG = BaselineConfig(
    agent_model="gpt-6-sol", reasoning_effort="medium", judge_model="gpt-6.1-sol"
)


class Judge(baseline.OfflineJudge):
    model = CONFIG.judge_model

    async def judge(self, request, response_type):
        labels = {
            "root_cause": "CORRECT",
            "grounding": "GROUNDED",
            "response_quality": "GOOD",
            "tool_selection": "APPROPRIATE",
        }
        return response_type(
            label=labels[request.evaluator],
            explanation="PRIVATE_JUDGE_TEXT",
            evidence_refs=["user_report"] if request.evaluator == "grounding" else [],
        )


class Provider:
    def __init__(self, changed=False):
        self.changed = changed

    async def next_decision(self, state):
        case = next(c for c in baseline.cases() if c.user_report == state.issue)
        diagnosis = DebugDiagnosis(
            status="INCONCLUSIVE", missing_evidence=["PRIVATE_OUTPUT"]
        )
        if self.changed and case.case_id == "inventory_validation_zero_01":
            diagnosis = DebugDiagnosis(
                status="RESOLVED",
                affected_endpoint=case.endpoint,
                root_cause="PRIVATE_OUTPUT",
                suggested_fix="PRIVATE_OUTPUT",
                evidence=[{"source": "api_spec", "finding": "PRIVATE_OUTPUT"}],
            )
        elif self.changed and case.case_kind == "inconclusive":
            diagnosis = DebugDiagnosis(
                status="NO_BUG_FOUND",
                evidence=[{"source": "api_spec", "finding": "PRIVATE_OUTPUT"}],
            )
        return FinalAnswerDecision(kind="final_answer", diagnosis=diagnosis)


@pytest.fixture
def sources(monkeypatch):
    current = compare.source_files()
    old = dict(current)
    name = "agent_service/agent/openai_provider.py"
    old[name] = old[name].replace(
        openai_provider.SYSTEM_INSTRUCTIONS.encode(), b"Original test instructions"
    )
    assert old[name] != current[name]
    real_read = compare.source_files
    monkeypatch.setattr(
        compare,
        "source_files",
        lambda ref=None: old if ref == "test-v1" else real_read(ref),
    )
    return old, current


@pytest.fixture
def reference(tmp_path, monkeypatch, sources):
    old, _ = sources
    with monkeypatch.context() as patch:
        patch.setattr(baseline, "source_digest", lambda: compare.source_checksum(old))
        directory = tmp_path / "v1"
        baseline.initialize(directory, CONFIG, mode="live")
        asyncio.run(baseline.collect_runs(directory, Provider))
        asyncio.run(baseline.evaluate_saved(directory, Judge()))
        report = save_report(directory)
    return directory, report.name


def archived(reference):
    return baseline.load_archived_report(*reference)


@pytest.fixture
def phoenix(monkeypatch):
    class Storage(InMemorySpanExporter):
        def shutdown(self):
            pass

    exporter = Storage()
    original = httpx.AsyncClient

    def respond(request):
        if request.url.path == "/healthz":
            return httpx.Response(200, text="OK")
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "name": span.name,
                        "parent_id": None if span.parent is None else "parent",
                        "context": {
                            "trace_id": f"{span.context.trace_id:032x}",
                            "span_id": f"{span.context.span_id:016x}",
                        },
                    }
                    for span in exporter.get_finished_spans()
                    if f"{span.context.trace_id:032x}"
                    == request.url.params.get("trace_id")
                    and f"{span.context.span_id:016x}"
                    == request.url.params.get("span_id")
                ]
            },
        )

    def client(**kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(respond))
        return original(**kwargs)

    monkeypatch.setattr(live.httpx, "AsyncClient", client)
    monkeypatch.setattr(
        live, "configure_tracing", lambda: configure_tracing(span_exporter=exporter)
    )
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE_TEST_KEY")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setattr(
        live, "AsyncOpenAI", lambda **_: pytest.fail("real paid provider")
    )
    return exporter


async def preflight(directory, reference):
    await live.preflight(
        directory,
        CONFIG,
        url="http://phoenix",
        variant="v2",
        v1_directory=reference[0],
        v1_report_id=reference[1],
        v1_source_ref="test-v1",
    )


@pytest.mark.asyncio
async def test_v2_identity_separation_reference_archive_and_paid_gate(
    tmp_path, reference, phoenix
):
    original = {p: p.read_bytes() for p in reference[0].rglob("*") if p.is_file()}
    directory = tmp_path / "v2"
    await preflight(directory, reference)
    manifest = baseline.load_manifest(directory)
    assert manifest.kind == "apilens-v2-experiment"
    assert manifest.experiment_id != archived(reference)["experiment"]["experiment_id"]
    assert (
        manifest.config == CONFIG
        and manifest.case_ids == archived(reference)["experiment"]["case_ids"]
    )
    assert (
        live.live_settings(directory)["project_name"]
        == f"apilens-v2-{manifest.experiment_id}"
    )
    assert not list((directory / "cases").glob("*"))
    assert (
        json.loads((directory / "preflight.json").read_text())["paid_calls_made"] == 0
    )
    assert len(phoenix.get_finished_spans()) == 1
    with pytest.raises(BaselineError, match="EXPLICIT_PAID_APPROVAL_REQUIRED"):
        await live.run_live(directory, approve_paid=False)
    with pytest.raises(FileExistsError):
        await preflight(directory, reference)
    assert baseline.load_archived_report(*reference) == archived(reference)
    assert all(p.read_bytes() == content for p, content in original.items())
    summary = baseline.load_archived_report(directory, save_report(directory).name)
    assert summary["kind"] == "apilens-v2-experiment-summary"
    assert "V2 experiment" in render_report(summary)


@pytest.mark.asyncio
async def test_v2_requires_original_reference_and_matching_config(
    tmp_path, reference, phoenix
):
    with pytest.raises(BaselineError, match="V1_REFERENCE_REQUIRED"):
        await live.preflight(
            tmp_path / "missing", CONFIG, url="http://phoenix", variant="v2"
        )
    with pytest.raises(BaselineError, match="COMPARISON_CONFIG_MISMATCH"):
        await live.preflight(
            tmp_path / "wrong",
            CONFIG.model_copy(update={"reasoning_effort": "high"}),
            url="http://phoenix",
            variant="v2",
            v1_directory=reference[0],
            v1_report_id=reference[1],
            v1_source_ref="test-v1",
        )
    assert not (tmp_path / "missing").exists() and not (tmp_path / "wrong").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift",
    ["source", "runtime", "config", "limits", "dataset", "reference", "project"],
)
async def test_v2_resume_refuses_drift_before_sdk(
    tmp_path, reference, phoenix, monkeypatch, drift
):
    directory = tmp_path / "v2"
    await preflight(directory, reference)
    if drift == "source":
        monkeypatch.setattr(baseline, "source_digest", lambda: "changed")
        error = "SOURCE_MISMATCH"
    elif drift == "runtime":
        monkeypatch.setattr(baseline, "runtime_versions", lambda: {"python": "future"})
        error = "RUNTIME_MISMATCH"
    else:
        name = {
            "config": "manifest.json",
            "limits": "manifest.json",
            "dataset": "manifest.json",
            "reference": "comparison.json",
            "project": "phoenix.json",
        }[drift]
        path = directory / name
        data = json.loads(path.read_text())
        if drift == "config":
            data["config"]["agent_model"] = "other-model"
            error = "COMPARISON_CONFIG_MISMATCH"
        elif drift == "limits":
            data["config"]["limits"]["max_tool_calls"] = 7
            error = "LIMITS_MISMATCH"
        elif drift == "dataset":
            data["dataset_sha256"] = "foreign"
            error = "DATASET_MISMATCH"
        elif drift == "reference":
            data["v1_report_id"] = "foreign"
            error = "COMPARISON_REFERENCE_MISMATCH"
        else:
            data["project_name"] = "apilens-v1-borrowed"
            error = "LIVE_PREFLIGHT_MISMATCH"
        path.write_text(json.dumps(data))
    with pytest.raises(BaselineError, match=error):
        await live.run_live(directory, approve_paid=True)
    assert not (directory / "cases").exists()


@pytest.mark.asyncio
async def test_comparison_outcomes_missing_metrics_and_privacy(
    tmp_path, reference, phoenix
):
    directory = tmp_path / "v2"
    await preflight(directory, reference)
    await baseline.collect_runs(directory, lambda: Provider(changed=True))
    await baseline.evaluate_saved(directory, Judge())
    report = save_report(directory)
    compared = compare.compare_saved(
        *reference, directory, report.name, v1_source_ref="test-v1"
    )
    assert compared["cohort"]["comparable"]
    changes = {r["case_id"]: r for r in compared["cases"]}
    assert changes["inventory_validation_zero_01"]["outcome_change"] == "IMPROVED"
    assert changes["insufficient_report_no_endpoint"]["outcome_change"] == "REGRESSED"
    assert changes["order_missing_inventory_01"]["outcome_change"] == "UNCHANGED"
    assert all(
        r["measurement_deltas"]["total_tokens"] is None for r in compared["cases"]
    )
    assert all(
        r["measurement_deltas"]["model_calls"] is None for r in compared["cases"]
    )
    assert (
        compared["aggregates"]["v2"]["evaluators"]["root_cause"]["expected_units"] == 18
    )
    assert "PRIVATE_" not in json.dumps(compared)
    assert compared["strict_success_delta"] == 0
    assert compared["outcome_changes"] == {
        "IMPROVED": 1,
        "UNCHANGED": 16,
        "REGRESSED": 1,
    }
    assert compared["structured_errors"]["v2"] == {
        "observed_cases": 18,
        "missing_cases": 0,
        "counts_observed": {},
    }


@pytest.mark.asyncio
async def test_v2_mock_live_run_resumes_without_repeating_started_work(
    tmp_path, reference, phoenix, monkeypatch
):
    directory = tmp_path / "v2"
    await preflight(directory, reference)
    constructed = []
    judgments = []

    class SDK:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class LiveJudge(Judge):
        def __init__(self, *, model, client):
            assert model == CONFIG.judge_model and isinstance(client, SDK)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def judge(self, request, response_type):
            judgments.append(request)
            return await super().judge(request, response_type)

    def provider(**kwargs):
        assert kwargs["model"] == CONFIG.agent_model
        assert kwargs["reasoning_effort"] == CONFIG.reasoning_effort
        constructed.append(kwargs)
        return Provider()

    monkeypatch.setattr(live, "AsyncOpenAI", lambda **_: SDK())
    monkeypatch.setattr(live, "OpenAIDecisionProvider", provider)
    monkeypatch.setattr(live, "OpenAIJudgeProvider", LiveJudge)
    await live.run_live(directory, approve_paid=True)
    assert len(constructed) == 18 and judgments
    retained = {
        p: p.read_bytes()
        for root in ("cases", "evaluations")
        for p in (directory / root).rglob("*.json")
    }
    count = len(judgments)
    await live.run_live(directory, approve_paid=True)
    assert len(constructed) == 18 and len(judgments) == count
    assert all(p.read_bytes() == content for p, content in retained.items())
    assert len(list((directory / "reports").iterdir())) == 2
    checks = list((directory / "trace-checks").glob("*.json"))
    assert len(checks) == 2
    assert all(
        row["state"] == "VERIFIED" for row in json.loads(checks[0].read_text())["cases"]
    )


@pytest.mark.asyncio
async def test_comparison_preserves_unstarted_interrupted_and_evaluator_error_cases(
    tmp_path, reference, phoenix
):
    directory = tmp_path / "v2"
    await preflight(directory, reference)
    first, second = baseline.cases()[:2]
    for case in (first, second):
        baseline.write_private(
            directory / "cases" / case.case_id / "started.json",
            {
                "experiment_id": baseline.load_manifest(directory).experiment_id,
                "case_id": case.case_id,
            },
        )
    await baseline.collect_runs(directory, Provider)
    await baseline.evaluate_saved(directory, Judge())
    # A separate evaluation revision is not needed: simulate a saved missing unit in a test-only bundle.
    case = baseline.cases()[2]
    result = directory / "evaluations/initial" / case.case_id / "result.json"
    data = json.loads(result.read_text())
    unit = next(r for r in data["results"] if r["evaluator"] == "grounding")
    unit.update(state="ERROR", label=None, error_type="MOCK_ERROR")
    data["convergence_eligibility"] = {
        "eligible": False,
        "reason": "GROUNDING_UNDETERMINED",
    }
    result.write_text(json.dumps(data))
    report = save_report(directory)
    compared = compare.compare_saved(
        *reference, directory, report.name, v1_source_ref="test-v1"
    )
    assert compared["cases"][0]["outcome_change"] == "UNDETERMINED"
    assert compared["cases"][0]["v2"]["execution_state"] == "INTERRUPTED"
    assert compared["cases"][2]["outcome_change"] == "UNDETERMINED"
    assert compared["cases"][2]["v2"]["evaluators"]["grounding"]["states"] == {
        "ERROR": 1
    }
    empty = tmp_path / "empty-v2"
    await preflight(empty, reference)
    pending = compare.compare_saved(
        *reference, empty, save_report(empty).name, v1_source_ref="test-v1"
    )
    assert all(r["outcome_change"] == "UNDETERMINED" for r in pending["cases"])
    assert (
        pending["aggregates"]["v2"]["measurements"]["model_calls"]["total_all_cases"]
        is None
    )
    assert pending["cases"][0]["v2"]["evaluators"]["root_cause"]["missing_units"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("dataset_sha256", "foreign"),
        ("fixture_id", "different"),
        ("case_ids", []),
        ("config", {}),
        ("mode", "offline"),
    ],
)
async def test_comparison_rejects_incompatible_cohorts(reference, field, value):
    left = archived(reference)
    right = copy.deepcopy(left)
    right["experiment"].update(kind="apilens-v2-experiment", experiment_id="v2")
    right["experiment"][field] = value
    with pytest.raises(BaselineError, match=f"COMPARISON_{field.upper()}_MISMATCH"):
        compare.check_cohort(left, right, v1_source_ref="test-v1")


@pytest.mark.asyncio
async def test_comparison_rejects_definition_and_source_drift_and_discloses_runtime(
    reference, sources, monkeypatch
):
    left = archived(reference)
    right = copy.deepcopy(left)
    right["experiment"].update(
        kind="apilens-v2-experiment",
        experiment_id="v2",
        source_sha256=baseline.source_digest(),
    )
    check = compare.check_cohort(left, right, v1_source_ref="test-v1")
    assert check["comparable"]
    right["experiment"]["runtime_versions"]["openai"] = "future"
    check = compare.check_cohort(left, right, v1_source_ref="test-v1")
    assert (
        not check["comparable"]
        and check["runtime_differences"]["openai"]["v2"] == "future"
    )
    right["experiment"]["runtime_versions"] = left["experiment"]["runtime_versions"]
    right["evaluator_versions_observed"]["grounding"] = ["future"]
    with pytest.raises(BaselineError, match="EVALUATOR_VERSIONS_MISMATCH"):
        compare.check_cohort(left, right, v1_source_ref="test-v1")
    right["evaluator_versions_observed"] = left["evaluator_versions_observed"]
    changed = dict(sources[1])
    changed["evaluation/qualitative.py"] += b"\n# changed evaluator definition\n"
    right["experiment"]["source_sha256"] = compare.source_checksum(changed)
    monkeypatch.setattr(
        compare,
        "source_files",
        lambda ref=None: sources[0] if ref == "test-v1" else changed,
    )
    with pytest.raises(BaselineError, match="CONTROLLED_DEFINITIONS_MISMATCH"):
        compare.check_cohort(left, right, v1_source_ref="test-v1")
    right["experiment"]["source_sha256"] = "foreign"
    with pytest.raises(BaselineError, match="COMPARISON_SOURCE_MISMATCH"):
        compare.check_cohort(left, right, v1_source_ref="test-v1")


def test_v2_smoke_cli_is_separate_and_offline(monkeypatch, capsys):
    monkeypatch.setattr(
        openai_provider, "AsyncOpenAI", lambda **_: pytest.fail("paid SDK")
    )
    baseline.BASELINES.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=baseline.BASELINES, prefix="test-v2-") as temporary:
        directory = Path(temporary) / "smoke"
        monkeypatch.setattr(
            sys, "argv", ["baseline", "smoke", str(directory), "--variant", "v2"]
        )
        baseline.main()
        assert baseline.load_manifest(directory).variant == "v2"
        summary = baseline.load_archived_report(
            directory, next((directory / "reports").iterdir()).name
        )
        assert "not V2 performance" in render_report(summary)
    assert "Private baseline report saved" in capsys.readouterr().out


def test_only_prompt_is_exempt_from_agent_contract_check(sources):
    old, current = sources
    assert (
        compare.controlled_definitions(old)[0]
        == compare.controlled_definitions(current)[0]
    )
    for name in (
        "agent_service/agent/openai_provider.py",
        "agent_service/agent/schemas.py",
        "agent_service/tools/endpoints.py",
        "agent_service/agent/state.py",
        "evaluation/metrics.py",
        "evaluation/baseline.py",
    ):
        changed = dict(current)
        if name.endswith("baseline.py"):
            changed[name] = changed[name].replace(
                b"database.reset()", b"database.initialize()"
            )
        elif name.endswith("openai_provider.py"):
            changed[name] = changed[name].replace(
                b'parallel_tool_calls": False', b'parallel_tool_calls": True'
            )
        else:
            changed[name] += b"\n# changed contract\n"
        assert changed[name] != current[name]
        assert (
            compare.controlled_definitions(old)[0]
            != compare.controlled_definitions(changed)[0]
        )


def test_reporting_pins_ignore_ast_display_and_empty_optional_fields(
    sources, monkeypatch
):
    content = sources[1]["evaluation/baseline_report.py"]
    tree = ast.parse(content)
    expected = compare._canonical_ast(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            # Emulate an added optional field in a newer Python AST schema.
            node._fields = (*node._fields, "future_optional_field")
            node.future_optional_field = []
    assert compare._canonical_ast(tree) == expected
    monkeypatch.setattr(
        ast, "dump", lambda *a, **k: pytest.fail("version-dependent display")
    )
    assert compare.reporting_fingerprint(content) == compare.CURRENT_REPORTING


@pytest.mark.asyncio
async def test_live_comparison_requires_the_preflight_bound_v1_report(
    tmp_path, reference, phoenix
):
    directory = tmp_path / "v2"
    await preflight(directory, reference)
    report_id = save_report(directory).name
    duplicate_id = str(uuid4())
    original = reference[0] / "reports" / reference[1] / "summary.json"
    baseline.write_private(
        reference[0] / "reports" / duplicate_id / "summary.json",
        json.loads(original.read_text()),
    )
    # Both reports are valid and identical, but only one was selected before execution.
    assert baseline.load_archived_report(reference[0], duplicate_id) == archived(
        reference
    )
    with pytest.raises(BaselineError, match="COMPARISON_REFERENCE_MISMATCH"):
        compare.compare_saved(
            reference[0], duplicate_id, directory, report_id, v1_source_ref="test-v1"
        )
    metadata = directory / "comparison.json"
    metadata.write_text(metadata.read_text() + "\n")
    with pytest.raises(BaselineError, match="COMPARISON_REFERENCE_MISMATCH"):
        compare.compare_saved(*reference, directory, report_id, v1_source_ref="test-v1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,before,after,error",
    [
        (
            "evaluation/baseline.py",
            b"return dataset\n",
            b'return [case.model_copy(update={"user_report": "changed"}) for case in dataset]\n',
            "CONTROLLED_DEFINITIONS_MISMATCH",
        ),
        (
            "evaluation/baseline.py",
            b"result = await evaluate_run(case, run, judge)",
            b'result = await evaluate_run(case.model_copy(update={"required_evidence": []}), run, judge)',
            "CONTROLLED_DEFINITIONS_MISMATCH",
        ),
        (
            "evaluation/baseline.py",
            b'ROOT / "evaluation" / "datasets" / "v1_cases.json"',
            b'ROOT / "other.json"',
            "CONTROLLED_DEFINITIONS_MISMATCH",
        ),
        (
            "evaluation/baseline_report.py",
            b'"path_length": len(run.decisions) + len(run.tool_attempts) if run else None,',
            b'"path_length": len(run.decisions) if run else None,',
            "REPORTING_DEFINITIONS_MISMATCH",
        ),
        (
            "evaluation/baseline_report.py",
            b"eligibility = assess_success(case, run, actual_results) if run else None",
            b'eligibility = SuccessEligibility(eligible=False, reason="changed")',
            "REPORTING_DEFINITIONS_MISMATCH",
        ),
    ],
)
async def test_source_gate_rejects_changed_effective_inputs_and_reporting(
    reference, sources, monkeypatch, path, before, after, error
):
    left = archived(reference)
    changed = dict(sources[1])
    assert before in changed[path]
    changed[path] = changed[path].replace(before, after, 1)
    right = copy.deepcopy(left)
    right["experiment"].update(
        kind="apilens-v2-experiment",
        experiment_id="v2",
        source_sha256=compare.source_checksum(changed),
    )
    monkeypatch.setattr(
        compare,
        "source_files",
        lambda ref=None: sources[0] if ref == "test-v1" else changed,
    )
    with pytest.raises(BaselineError, match=error):
        compare.check_cohort(left, right, v1_source_ref="test-v1")
