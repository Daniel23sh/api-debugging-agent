# Phase 8 — fixed V1 baseline

Phase 8A supplies offline-verifiable execution, persistence and reporting. Phase
8B requires a separately approved live run and verified Phoenix trace availability.
The frozen Phase 6 dataset and Phase 7 evaluators are reused without changing the
agent, prompts, limits, fixtures or evaluator semantics.

The approved live run is recorded in the [2026-10-18 V1 baseline report](reports/v1-baseline-2026-10-18.md),
with machine-readable aggregates alongside it. Raw artifacts remain ignored.

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

Only executions with a committed `RETURNED` outcome contribute to experiment
success and convergence. A diagnosis snapshot published before an interruption
still exposes its status, evidence, measurements and evaluator labels, but its
report eligibility is `EXECUTION_NOT_RETURNED` and its convergence is absent.

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

### Historical read-only access

`summary` recomputes measurements and writes a new report, so it retains the exact
source/runtime checks used by collection, resumption and evaluation. After a
legitimate source or environment change, read an existing saved report instead:

```bash
python -m evaluation.baseline archive evaluation/artifacts/baseline/v1-01 \
  --report-id REPORT_UUID
```

Replace `REPORT_UUID` with the directory name under that experiment's `reports/`.
The archive command prints the saved JSON unchanged and writes nothing. It needs
no OpenAI credentials and performs no judging, run-metric extraction, eligibility
or convergence reassessment. Aggregate counts, denominators and measurements are
checked arithmetically against the saved case rows, preserving missing values;
only floating-point statistics allow a small rounding tolerance. No later data
or current evaluator decisions enter these checks. Original source/runtime and evaluator versions remain
attached to the results. A saved report retains its original missing/pending data
even if collection or evaluation later appends more results. Manifest, dataset,
snapshot, model, judge and recorded
result bindings are still checked; archival access does not authorize execution
or waive source/runtime checks on any existing mutation operation. It supports
the existing serialized schema, without migrations or reinterpretation of older
evaluator labels. Use the historical checkout if that schema is no longer supported.

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

## Live preflight and explicit paid execution

Start a local Phoenix server separately; keep its database under ignored local
artifacts. The server must support `GET /v1/projects/{project}/spans` with
`trace_id` and `span_id` filters (verified with Phoenix 20.23.0). The minimal
integration uses an unauthenticated local Phoenix origin and OTLP/HTTP; it does
not create Phoenix experiment or dataset records.

```bash
python -m evaluation.baseline_live preflight \
  evaluation/artifacts/baseline/v1-01 --judge-model gpt-6.1-sol \
  --phoenix-url http://127.0.0.1:6006
```

Preflight loads the ignored `.env`, checks that an API key is present without
sending it anywhere, and requires the normal OpenAI API origin. It verifies the
18 fixed cases, fixtures, ordered setup and all 17 executable reproductions
against isolated in-process Sandbox databases. It freezes the current V1 agent
model/reasoning configuration and the explicitly selected judge model. It then
exports a synthetic, content-free span through the existing production batch
exporter and retrieves that exact root span from Phoenix. It makes **zero OpenAI
calls**. A failed preflight must not be used for execution; choose a new directory
rather than editing its immutable files.

The manifest, `preflight.json` and `phoenix.json` belong to this experiment only.
A unique Phoenix project `apilens-v1-<experiment-id>` separates the baseline from
calibration and other experiments. Source/runtime checks also apply to live
commands. The saved Git revision identifies the parent checkout; `source_sha256`
identifies the exact Python/config contents, including reviewed uncommitted 8B
implementation that cannot be committed before the real-result review gate.

**Obtain explicit human approval for these models and the usage estimate first.**
Only then run:

```bash
python -m evaluation.baseline_live run \
  evaluation/artifacts/baseline/v1-01 --approve-paid
```

This flag is a deliberate operator acknowledgment, not authorization by itself.
The command rechecks the saved Phoenix probe before constructing an OpenAI
client. It uses the frozen agent/judge models with existing V1 behavior, SDK
retries disabled, app-owned tracing and the existing telemetry privacy boundary.
After collection and evaluation it flushes/shuts down instrumentation, saves the
report, and verifies every recorded DebugSession root in Phoenix. Judge SDK calls
are outside the agent recorder and its system metrics. Paid usage/output length
is model-dependent; no monetary cap is implied by preflight or this flag.

Repeating this command resumes unfinished work, skipping every started agent
case and already started/completed judgment. Do not use a new directory to repeat
completed paid cases to improve outcomes. Additional paid execution or a new paid
evaluation revision requires separate human approval.

The following commands are read-only with respect to OpenAI and can be repeated:

```bash
python -m evaluation.baseline summary evaluation/artifacts/baseline/v1-01
python -m evaluation.baseline_live verify-traces evaluation/artifacts/baseline/v1-01
```

Each trace check is append-only under `trace-checks/`. A `VERIFIED` result means
Phoenix returned the exact trace/span ID, expected root name and absent parent.
The saved URL is the actual Phoenix REST span-query link, suitable for inspecting
failed investigations; the Phoenix UI can also search the same trace ID in the
recorded project. `NO_TRACE` and `NOT_FOUND` stay explicit. An HTTP export success
or a UI HTTP-200 page alone is not accepted as trace availability. Never commit
these private server responses, snapshots, bundles, or local Phoenix storage.

`run` and `verify-traces` exit unsuccessfully if any required case is `NO_TRACE`
or `NOT_FOUND`, after preserving the append-only trace check. This conservatively
leaves tracing incomplete even for an early setup failure that produced no trace;
the execution failure and all available results remain recorded. Network/server
query errors also exit with a safe error. Resolve ingestion and use `verify-traces`
to check again without agent or judge calls. Never recollect or rejudge completed
cases to repair missing telemetry. Historical archive reads require no Phoenix server.
