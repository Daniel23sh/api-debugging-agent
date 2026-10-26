"""V3 is an independent current-system measurement, never a rewritten V1/V2."""

import copy
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from evaluation import baseline, baseline_live, baseline_report, compare


@pytest.mark.parametrize(("variant", "kind", "title"), [
    ("v1", "apilens-v1-baseline", "V1 baseline"),
    ("v2", "apilens-v2-experiment", "V2 experiment"),
    ("v3", "apilens-v3-verification", "V3 current-system verification"),
])
def test_variants_keep_distinct_identities_and_report_titles(tmp_path, variant, kind, title):
    config = baseline.BaselineConfig(
        agent_model="offline", reasoning_effort="medium", judge_model="offline"
    )
    directory = tmp_path / variant
    manifest = baseline.initialize(directory, config, mode="live", variant=variant)
    assert manifest.kind == kind
    assert baseline.load_manifest(directory).variant == variant
    assert manifest.case_ids == [case.case_id for case in baseline.cases()]
    summary = baseline_report.build_summary(directory)
    assert summary["kind"] == f"{kind}-summary"
    assert baseline_report.render_report(summary).startswith(f"# {title}\n")
    with pytest.raises(FileExistsError):
        baseline.initialize(directory, config, mode="live", variant=variant)


def test_v3_smoke_is_offline_complete_archivable_and_not_v1_v2(monkeypatch):
    with TemporaryDirectory(dir=baseline.BASELINES, prefix="test-v3-") as temporary:
        def no_paid_calls(*args, **kwargs):
            pytest.fail("Offline smoke must not construct a live SDK/provider")

        monkeypatch.setattr(baseline_live, "AsyncOpenAI", no_paid_calls)
        monkeypatch.setattr(baseline_live, "OpenAIDecisionProvider", no_paid_calls)
        directory = Path(temporary) / "v3-offline"
        monkeypatch.setattr(
            sys, "argv", ["baseline", "smoke", str(directory), "--variant", "v3"]
        )
        baseline.main()
        report = next((directory / "reports").iterdir())
        summary = baseline.load_archived_report(directory, report.name)
        assert summary["total_cases"] == 18
        assert summary["execution_states"] == {"RETURNED": 18}
        assert summary["evaluation_states"] == {"COMPLETE": 18}
        assert summary["experiment"]["kind"] == "apilens-v3-verification"
        assert "OFFLINE SMOKE — not V3 performance results" in (report / "report.md").read_text()
        assert json.loads((directory / "manifest.json").read_text())["mode"] == "offline"
        with pytest.raises(baseline.BaselineError, match="V1_V2_EXPERIMENTS_REQUIRED"):
            compare.check_cohort(summary, copy.deepcopy(summary), v1_source_ref="unused")
        before = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
        monkeypatch.setattr(baseline, "source_digest", lambda: "changed-source")
        assert baseline.load_archived_report(directory, report.name) == summary
        with pytest.raises(baseline.BaselineError, match="SOURCE_MISMATCH"):
            baseline.load_manifest(directory)
        assert before == {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}


def test_historical_v2_reader_fingerprint_remains_explicit():
    assert compare.V2_READERS == "36150dda491618c7f5e3a4961bc150d4177c301d8a99953e89387f3f284df300"
    assert compare.reader_fingerprint(
        (baseline.ROOT / "evaluation/baseline.py").read_bytes()
    ) == compare.CURRENT_READERS
