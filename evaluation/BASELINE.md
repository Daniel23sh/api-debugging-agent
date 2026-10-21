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

## Phase 10 — controlled V2 preparation and later execution

Phase 10A changes only `SYSTEM_INSTRUCTIONS` in the agent: seek approved Sandbox
runtime evidence when a causal claim depends on unobserved state. Approved
POST/PATCH requests may change isolated Sandbox data; source-code changes and
external/production actions remain prohibited. Static diagnosis remains valid
when its evidence is sufficient. Provider parsing/correction, model/settings,
tools/permissions, limits, fixture/setup, dataset, evaluators and metric definitions
are unchanged. Model-quality effects remain unmeasured until a separately approved
Phase 10B experiment. See the [selected hypothesis](reports/v1-error-analysis.md#9-one-v2-target-and-testable-hypothesis).

The existing runner accepts `--variant v2` at creation; default V1 commands retain
their behavior. V2 uses manifest kind `apilens-v2-experiment`, summary kind
`apilens-v2-experiment-summary`, a new experiment UUID, a fresh dedicated directory,
and Phoenix project `apilens-v2-<experiment-id>`. Existing directories are refused.
All fixture isolation, append-only snapshots/results, source/runtime checks,
typed failures, paid gate, resume behavior, tracing and privacy controls are shared.
The variant property adds no field to historical V1 manifests. Archival reads
still validate saved bindings and arithmetic without rerunning evaluator semantics.

V2 live preflight additionally requires the original V1 directory, saved report
UUID and a Git source ref. The ref must reconstruct the **entire** recorded V1
source/config checksum; the recorded parent alone is insufficient. Commit
`a41786621ff79e16624f5cbdf51878ee47a31d3e` matches the exact frozen source checksum
`5e4f455833b30a8ad4e6e8bcd51d3ed23ffb25368d4d14190f249f2d8c859dc5`.
Keep that Git history available locally; no checkout or network fetch is performed
by comparison. Both full source identities remain attached to the experiments.

`evaluation.compare` checks matching dataset hash/order, fixture, mode, models,
reasoning, limits, initial evaluation revision and observed evaluator versions.
It compares all agent/Sandbox sources and the core recorder, evaluator and metric
sources, plus effective dataset loading, fixture/setup execution, evaluator
orchestration and their bindings. The provider AST
(Python's parsed code structure) is compared with only `SYSTEM_INSTRUCTIONS`
replaced by a placeholder; changes to parsing, tool descriptions or safety code
therefore fail that check. Reporting imports, arithmetic, case rows and eligibility
are separately pinned to the approved AST fingerprints: original collection and
the Phase 8 repairs for V1; only the repaired implementation for V2. Rendering and
experiment-kind metadata are excluded. These explicit pins allow the known
reporting refactor without claiming general semantic equivalence of arbitrary
code. Changes to reporting semantics require a separately reviewed decision.
The existing Phase 8 interruption-reporting correction is retained: V1's 18
returned executions and original judgments are preserved, while a V2 interruption
cannot become a strict success. No historical eligibility is recomputed.

The private V2 `comparison.json` pins the original saved-report checksum and
reference location/identity; its checksum is frozen in `preflight.json`. The paid
runner revalidates that binding and current comparability before constructing a
client. Final live comparison also checks that the selected V1 report is exactly
the preflight-bound report, even if another report has identical measurements.
Changing source/config/runtime or borrowing another experiment's preflight/project
is refused. Retain original private artifacts in place. Runtime/dependency
differences are reported by comparison as a confound with no outcome comparisons;
V2 live preflight refuses them. Resolve drift before seeking paid approval.
Only package versions recorded by V1 can be directly compared; unchanged dependency
pin files provide additional evidence, not a complete historical installation
inventory for every transitive package.

### Phase 10B inputs and commands (not authorization)

Use the existing ignored `.env` for credentials, with the normal OpenAI API origin.
Keep `OPENAI_MODEL=gpt-6-sol`, `OPENAI_REASONING_EFFORT=medium`, and explicit judge
`gpt-6.1-sol`; use the frozen runtime/dependencies. Start the retained local Phoenix
service separately, preserving its storage. Choose an unused V2 output directory.
After the final source has stopped changing, this preflight makes no OpenAI calls:

```bash
python -m evaluation.baseline_live preflight \
  evaluation/artifacts/baseline/v2-01 --variant v2 \
  --judge-model gpt-6.1-sol --phoenix-url http://127.0.0.1:6006 \
  --v1-directory evaluation/artifacts/baseline/v1-2026-10-18-01 \
  --v1-report-id 3afd2466-3ba3-4901-b7bd-08ec7818967f \
  --v1-source-ref a41786621ff79e16624f5cbdf51878ee47a31d3e
```

Review the saved identity, settings, comparison binding and usage estimate, then
obtain explicit human approval for the full run. Preflight is not approval. Only
after that separate approval, Phase 10B may execute:

```bash
python -m evaluation.baseline_live run evaluation/artifacts/baseline/v2-01 --approve-paid
```

The same command resumes without repeating any started agent case or judgment.
Do not repair an interrupted case by choosing a fresh directory, running selected
paid pretests, or rejudging V1. Trace verification remains required and uses the
dedicated V2 project. Final source changes after preflight require a new preflight
before any paid work; do not change sources mid-experiment.

After the actual V2 results exist, obtain its saved report UUID under `reports/`
and compare **saved initial results** without running agents or judges:

```bash
python -m evaluation.compare \
  evaluation/artifacts/baseline/v1-2026-10-18-01 evaluation/artifacts/baseline/v2-01 \
  --v1-report-id 3afd2466-3ba3-4901-b7bd-08ec7818967f \
  --v2-report-id V2_REPORT_UUID \
  --v1-source-ref a41786621ff79e16624f5cbdf51878ee47a31d3e
```

By default V2 source verification uses the current working sources. After later
source changes, supply `--v2-source-ref COMMIT_WITH_EXACT_V2_SOURCE`; its checksum
must match the V2 manifest. Git parent identity alone is not sufficient when the
run included uncommitted source. Never bypass source validation to force a match.

Comparison prints deterministic sanitized JSON: saved aggregates and case rows,
existing strict-success changes/failure reasons, status pairs, all six evaluator
families, trajectory/convergence, calls/latency/tokens, and structured errors with
coverage. It preserves scored/not-applicable/undetermined/error states, missing
units, and unknown numeric values. Missing/interrupted/evaluator-error outcomes
are `UNDETERMINED`, and unknown deltas stay null rather than becoming zeros or
regressions. Convergence stays within each experiment; no pooled ratio or new
quality score is introduced. Judge explanations, diagnosis text, source/log bodies
and tool arguments are excluded. Intentionally review output before publishing;
Phase 10A creates no public V2 results report or predicted measurements.

Offline plumbing remains available with
`python -m evaluation.baseline smoke evaluation/artifacts/baseline/v2-smoke-01 --variant v2`.
It is prominently labeled as offline and is not model-quality evidence. Focused
tests also exercise mock V2 live orchestration, paid refusal, resume, original
archive reads, cohort/source drift, missing data and privacy without real OpenAI
requests.
