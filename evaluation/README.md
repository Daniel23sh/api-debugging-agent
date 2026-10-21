# Phase 7 evaluation

The full-dataset Phase 8 workflow is documented in [BASELINE.md](BASELINE.md).
Calibration remains limited to its six approved cases.

`EvaluationCase` remains the Phase 6 expected behavior. `EvaluationRun` is an
opt-in snapshot of actual agent activity. `EvaluatorResult` judges one decision
or the entire run. The public `/debug` request and decision-provider interface
are unchanged.

Use `EvaluationRecorder(case_id)` with the internal
`run_debug_session(..., recorder=recorder)` or `run_agent(..., recorder=recorder)`.
Afterwards, `recorder.snapshot()` returns an independent typed copy. Recorders
belong to one session; no recording happens by default. Decision records link
only to observations available beforehand. Rejected decisions and failed model
attempts remain visible even when agent counters do not increment.

`await evaluate_run(case, snapshot, judge)` in `evaluation.orchestration` returns
the six evaluator families, system metrics and success eligibility. It does not
run the agent, create an experiment, annotate Phoenix, or produce one aggregate
quality score. Output contains case/session/trace and decision/span references.

## Results and judges

Results distinguish `SCORED`, `NOT_APPLICABLE`, `UNDETERMINED` and `ERROR`.
Missing observations are not a failing quality label. A judge API failure is an
evaluator error, not an agent failure. Each scored evaluator has a discrete enum
label; trajectory metrics are structured numbers.

Tool selection is evaluated in the context of each decision. Dataset tool lists
are guidance, never a mandatory sequence. Deterministic checks handle clear
choices; only ambiguous selection uses the judge. Tool arguments are evaluated
separately using the actual schemas, endpoint permissions, canonical case
requests and earlier request IDs. A legal exploratory request can remain
undetermined. A canonical quantity-zero request is correct evaluation input
even though the buggy Sandbox rejects it.

Root cause compares causal mechanisms with expected truth. Grounding compares
diagnosis claims with actual collected evidence, including inconclusive claims
about missing information. Response quality judges clarity and usefulness
without seeing the expected root cause. These are intentionally independent.
Grounding and quality rubric version 2 clarifies that an application-confirmed
runtime failure may be grounded but still be a poor response. Grounding does not
penalize an answer merely because the agent could have reached a better outcome;
quality treats an internal-failure-only message without useful guidance as poor.

`JudgeProvider` is injectable for offline tests. The live `OpenAIJudgeProvider`
requires an explicit `EVAL_JUDGE_MODEL` (or explicit constructor model), uses
Responses structured output, a 30-second evaluation deadline, no API-error
retries and at most one malformed-output correction. Serialized input including
instructions is bounded to 64,000 characters; oversize input returns
`ERROR/INPUT_TOO_LARGE`, without truncation. Explanations are at most 500
characters. Grounding references must point to supplied observations/report.
No hidden reasoning is requested or retained.

Judge inputs contain only the context each rubric needs. Arbitrary headers,
credential fields and failure-message bodies are excluded from tool evidence.
Reports and source/log evidence are private local evaluation data; do not add
credentials to reports. OpenAI instrumentation uses the existing app-owned
provider, hidden inputs/outputs and error-export sanitizer. Judge text is never
added to span attributes. Library callers own instrumentation setup/cleanup;
the calibration commands manage it automatically when tracing is enabled.

## Metrics and convergence

Path length is **decision attempts + actual tool execution attempts**. Internal
model corrections increase SDK-call count, not decision count. Identical calls,
non-retry duplicates, timeout retries and calls independently judged unnecessary
are reported separately; a duplicate is not automatically waste.

Agent-only latency uses a monotonic clock. SDK-call counts and token usage come
from actual agent requests, never from decision counts or judge calls. Missing
usage stays `None`; partial per-call usage is retained. Aggregate totals require
complete input/output/total observations. Cached and reasoning tokens are
subsets and are never added again. Structured error counts count recorded error
events by type/scope, not unique causal incidents.

`trajectory.convergence(cases_by_id, runs, results_by_session)` compares successful
runs only: expected final status, root `CORRECT` when applicable, and grounding
`GROUNDED`. Missing/error judgments cannot qualify. Matching paraphrase group,
dataset/fixture, agent source version, model/settings, limits and evaluator
versions/judge model are required. Incomplete comparison context leaves the
ratio absent with a reason. The ratio is shortest successful observed path
divided by current length. A single successful run scores one; equal-length
alternate paths tie. Shared unnecessary steps can survive in every run. One
therefore does not establish a global optimum or prove correctness.

## Six-case human calibration

Phase 7 cannot close until real human labeling and disagreement review finish.
Automated mocked tests do not substitute for this step. There is no full-dataset
run or `--all` option here; Phase 8 remains separate.

Set `OPENAI_API_KEY`, the agent settings and **`EVAL_JUDGE_MODEL` explicitly** in
`.env`. The following command makes paid model calls for exactly the six approved
calibration cases, serially. It uses a temporary in-process Sandbox, resets its
fixtures before every case, and executes each case's setup requests in order:

```bash
python -m evaluation.calibration collect evaluation/artifacts/calibration-01
```

Snapshots are saved as each run completes. Artifacts are ignored by Git and
created with owner-only file permissions; treat them as private. The command
refuses to overwrite an existing collection directory. Judge results are saved
separately in `judge-results.json`. Do not inspect that file before labeling.

1. Review `human-review.json`: reports, diagnoses, actual observations, expected
   status and ground truth, without judge labels.
2. Fill all six entries in `human-labels.json` using root labels
   `CORRECT/PARTIALLY_CORRECT/INCORRECT` (or `NOT_APPLICABLE` for non-bug cases),
   grounding `GROUNDED/PARTIALLY_GROUNDED/UNGROUNDED`, quality
   `GOOD/NEEDS_IMPROVEMENT/POOR`, and your notes.
3. Reveal the comparison only after all human labels exist:

```bash
python -m evaluation.calibration compare evaluation/artifacts/calibration-01
```

Review recurring disagreements. Change the rubric/evaluator when warranted,
never the agent as part of calibration. Re-evaluate saved snapshots without
re-executing the agent; choose a fresh output filename to preserve prior results:

```bash
python -m evaluation.calibration rejudge evaluation/artifacts/calibration-01 --results judge-results-02.json
```

Create `review.json` in the artifact directory with a resolution for each
disagreement (case_id, evaluator, category and resolution), your comment, and
`no_material_unresolved_pattern: true` only after genuine human confirmation.
Categories are `human_label`, `rubric`, `judge_error`, `isolated_disagreement`.
Missing/error judge results must be re-evaluated before closing calibration.
Resolutions must match the exact comparison selected by `--results`: a review
with no resolutions from a later agreeing judgment cannot finalize an earlier
judgment with disagreements. Preserve a copy of each review with its result file
when switching versions. Finalization permits evidence-backed reviewed
disagreements; it preserves both labels and does not require full agreement.
Calibration completion does not make an incorrect agent final status successful.
Example shape, with no fabricated human assessment:

```json
{
  "resolutions": [],
  "no_material_unresolved_pattern": false,
  "comment": ""
}
```

After completing that review:

```bash
python -m evaluation.calibration finalize evaluation/artifacts/calibration-01 --results judge-results-02.json
```

`finalize` prints the comparison and explicit human review; redirect it to a
local artifact if desired. No arbitrary agreement percentage is required. No
human labels, approvals or real results are committed or generated by tests.

## Offline verification

```bash
python -m pytest
ruff check .
git diff --check
```

Tests use local/mocked providers only, including SDK HTTP errors and tracing.
The Phase 6 dataset, contract and reproducibility tests remain unchanged.
