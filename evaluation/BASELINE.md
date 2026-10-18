# Phase 8 — fixed V1 baseline

Phase 8A supplies offline-verifiable execution, persistence and reporting. Phase
8B requires a separately approved live run and verified Phoenix trace availability.
The frozen Phase 6 dataset and Phase 7 evaluators are reused without changing the
agent, prompts, limits, fixtures or evaluator semantics.

## Offline verification

From the editable project environment:

```bash
python -m evaluation.baseline smoke evaluation/artifacts/baseline/smoke-01
python -m evaluation.baseline summary evaluation/artifacts/baseline/smoke-01
python -m pytest
ruff check .
git diff --check
```

`smoke` uses explicit local mock decisions and judgments, plus the real in-process
Sandbox. It needs no API key or Phoenix server and never makes OpenAI calls. It
runs all 18 cases. Its outcomes are **not V1 performance measurements**. This
mode is saved in the manifest and printed prominently in the generated report.

## Data flow and files

`initialize(directory, config, mode=...)` freezes the experiment identity, all 18
case IDs, model/settings, default agent limits, source and dataset checksums,
fixture identity, Git revision and actual Python/dependency versions. Existing
directories are refused, including calibration directories. CLI output belongs
under the ignored `evaluation/artifacts/baseline/` namespace.

`collect_runs(directory, provider_factory, tracer=...)` executes serially through
the existing `run_debug_session` interface and `EvaluationRecorder`. Each case
gets its own temporary SQLite database, initialization/reset verification and
ordered dataset setup requests. The real agent remains responsible for choosing
its investigation, including which reproduction requests to make.

Each case has an immutable `started.json`, optional `run.json`, and terminal
`execution.json`. The snapshot preserves actual session/trace/span identifiers,
decisions, tool attempts, evidence, safe structured errors and observed agent
metrics. Provider/setup exceptions can leave no snapshot, but still have a case
outcome. Normal `INCONCLUSIVE`/`LIMIT_REACHED` responses remain returned outcomes,
not infrastructure exceptions or automatically successful investigations.

`evaluate_saved(directory, judge, revision="initial")` reads snapshots and reuses
`evaluate_run`. Each evaluation revision has its own identity metadata and
per-case start/result/error files. Judgments and evaluator errors remain separate
from execution outcomes. SDK usage is captured only within the agent recorder;
judge calls never enter agent metrics.

`build_summary`/`save_report` reuse the existing system metrics and successful-only
convergence calculation. Every case appears, including pending, interrupted,
setup/agent exceptions, missing diagnoses, evaluator failures and mismatches.
The report includes six evaluator families, scored-label distributions,
unavailable states and missing-unit denominators. Numeric totals for all cases
are absent when required measurements are missing; observed-only sums and means
carry explicit coverage. Trace IDs alone do not claim Phoenix ingestion.

Reports are append-only under `reports/<id>/summary.json` and `report.md`.
They contain controlled counts/labels, versions and identifiers, without reports,
diagnosis prose, source/log contents, tool arguments or judge explanations.
Full private snapshots and evaluation bundles remain separate. Never commit raw
artifacts. Public aggregate reporting must be intentionally reviewed in Phase 8B.

## Interruptions, resume and re-evaluation

Reusing the same directory/config skips all started agent cases and completed or
started judgments. It never automatically repeats potentially paid work. A case
interrupted through cancellation retains its partial recorder snapshot. After a
hard kill, a start marker without a terminal outcome is marked `INTERRUPTED` on
resume; in-progress data may be unavailable. Prior completed snapshots remain
intact. Such a case is not silently retried to improve results.

An exclusive `.lock` prevents concurrent writers. If the process was forcibly
killed, verify it has stopped before manually removing its stale lock. Source,
dataset, runtime and snapshot checksum mismatches stop reuse. Do not edit
snapshots or copy files from calibration/other experiments into a baseline.

Evaluation failures and interrupted judgments are retained when resuming. A
deliberate re-evaluation uses a new explicit revision and preserves earlier
results. This does not execute the agent again. Any additional paid judging or
agent execution still needs explicit approval.

Library injection supports offline integration tests; live orchestration must
freeze and explicitly construct the configured agent and judge providers. No
automatic model fallback or full-dataset execution exists in calibration tools.

## Stage gates

8A: independent read-only code review, full pytest/Ruff/diff verification, then a
focused commit. 8B follows only after 8A passes. Before any paid requests, 8B must
preflight all cases/configuration/output paths and Phoenix, then obtain explicit
user approval for the selected models and usage estimate. No baseline is complete
until actual outcomes, verified trace availability, aggregate report and separate
independent result review are finished. Phase 9 is outside this workflow.
