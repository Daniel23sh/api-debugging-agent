# Evaluation

The full-dataset collection/archive workflow is documented in
[BASELINE.md](BASELINE.md). The six-case calibration procedure below remains
separate from full-dataset experiments.

## Recorded results

- [Historical V1/V2 experiment](reports/v1-v2-experiment-2026-10-21.md):
  13/18 → 15/18 strict successes; the original results remain unchanged.
- [Current-system V3 verification](reports/v3-verification-2026-10-25.md):
  17/18 strict successes on 2026-10-25, with 18/18 expected status matches.
  Both no-bug cases passed and all 15 V2 strict successes were retained.
  [Protocol and completion record](V3.md) ·
  [Saved summary](reports/v3-verification-2026-10-25.json).

V3 is a separate current-system measurement, not a controlled schema-only
intervention or a replacement for V1/V2. One run per case does not establish
reliability. The remaining partial-grounding case and judge evidence-view
limitation are documented in its report. These records do not authorize new
paid executions.

## Recording and evaluator interfaces

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

Calibration completion requires real human labeling and disagreement review.
Automated mocked tests do not substitute for this step. The calibration command
has no full-dataset run or `--all` option; use the separate baseline workflow.

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
python -m pip install -c constraints-dev.txt -e ".[dev]"
ruff check .
python -m mypy
PYTHON_DOTENV_DISABLED=1 OPENAI_API_KEY= EVAL_JUDGE_MODEL= PHOENIX_COLLECTOR_ENDPOINT= python -m pytest
git diff --check
```

Tests use local/mocked providers only, including SDK HTTP errors and tracing.
The Phase 6 dataset, contract and reproducibility tests remain unchanged.

### Continuous integration (Phase 11B)

`.github/workflows/ci.yml` runs one Ubuntu/Python 3.13 job for pull requests to
`main` and pushes to `main`: constrained development installation, Ruff, Mypy,
then full pytest (including Golden tests once). The same `checks` job also uses
Node.js 22 to run `npm ci`, `npm run typecheck`, `npm test` and `npm run build`
from `frontend/`. Frontend tests mock network calls. Any command failure fails
the job. Repository permissions are read-only; no secrets or services are configured.
The test environment disables `.env` loading and blanks agent/judge credentials
and the Phoenix collector endpoint. Dependency installation uses the network;
application tests use mocked providers, in-process HTTP and temporary SQLite.

Mypy is a Python-only development dependency. `python -m mypy` reads the explicit
strict scope in `pyproject.toml`:

- `agent_service/main.py` and `demo.py`: HTTP entry points, the demo response
  contract, recording and disposable Sandbox setup.
- `agent_service/agent/schemas.py` and `state.py`: decisions, diagnoses,
  observations, session transitions and execution limits.
- `agent_service/tools/contracts.py` and `endpoints.py`: tool results and approved
  endpoint normalization.
- `sandbox_api/models.py` and `source_map.py`: data models and source allowlists.
- `evaluation/dataset.py`: canonical dataset loading and case validation.

`follow_imports = "silent"` retains imported types while limiting reported errors
to these roots ([Mypy import handling](https://mypy.readthedocs.io/en/stable/running_mypy.html#following-imports)).
SDK integration, dispatch/loop implementation, observability, SQLite internals,
evaluator/experiment code and tests are outside the initial scope. Their dynamic
payloads and existing annotation gaps require separate adoption work; this is
not whole-project type verification. No global missing-import suppression or
production behavior changes are needed.

These checks protect deterministic system contracts. They do not measure live
LLM quality or run baseline, rejudging or comparison experiments. Live evaluation
remains an explicit human-approved activity; see the
[live evaluation runbook](BASELINE.md#phase-11c--live-evaluation-runbook).

### Merge gates and repository configuration (Phase 11C)

Before merging, require all of the following on the current PR revision:

| Command | Required protection |
|---|---|
| `ruff check .` | Lint checks |
| `python -m mypy` | The strict production scope above |
| `python -m pytest` | All unit, integration, contract, safety and Golden tests |
| `npm ci` (from `frontend/`) | Frontend installation from the committed lockfile |
| `npm run typecheck` (from `frontend/`) | Frontend TypeScript contracts |
| `npm test` (from `frontend/`) | Frontend behavior with mocked network calls |
| `npm run build` (from `frontend/`) | Frontend production compilation |

The full suite includes deterministic security boundaries such as rejected tool
arguments, approved endpoint restrictions and agent execution limits, plus real
Sandbox HTTP/SQLite integration. It is not a comprehensive security audit. The
Golden subset is included automatically; its separate command is useful locally,
not a second CI gate. Do not accept skipped, cancelled, pending or failed checks
as completion, and review changes to the workflow, type scope and test selection.

The workflow is named `CI`. Its only job is `checks` (no separate job `name`),
so the check-run name/status context is **`checks`**, normally displayed under
`CI / checks`. Lint, typing and tests are steps within this one check, not three
independent required-check names. A nonzero exit fails the job and prevents later
steps from running. No path filters, conditional skips or `continue-on-error`
are configured. Renaming this job requires updating the repository requirement.

A failing Actions job alone does **not** prevent merging. A repository
administrator must configure `main` in Settings → Rules → Rulesets (or the
equivalent branch-protection rule):

1. Target `main` (the default branch), set enforcement to **Active**, and require
   a pull request before merging. Preserve existing deletion/force-push rules.
2. Enable **Require status checks to pass**, add the exact `checks` context after
   a PR run has reported it, and select **GitHub Actions** as its expected source.
3. Enable **Require branches to be up to date before merging**. Keep bypass
   actors empty so the check requirement cannot be routinely bypassed. If the
   base advances, arrange a separately approved branch update and rerun CI.
4. Verify the latest PR's Checks tab shows the `CI` run and successful `checks`
   job, inspect that all required steps actually ran, and confirm the merge box
   treats it as required. A green local test run does not establish this.

On 2026-10-22 the readable `Protect main` ruleset was Active, targeted the default
branch, required a PR and blocked deletion/force pushes, with no bypass actors;
it did **not** require status checks. The connected integration could not read
classic protection (403); the local GitHub CLI confirmed no classic rule (404).
No settings were changed, and CI merge
enforcement must not be claimed until an administrator verifies the requirement.
Recheck the current settings rather than treating this dated observation as live
state. See [GitHub's required-check guidance](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks).
The current workflow has no `merge_group` trigger; adopting a merge queue needs a
separately reviewed workflow update before requiring this check in that queue.

### Golden regression subset (Phase 11A)

```bash
python -m pytest -m golden
```

`tests/test_golden_regression.py` selects seven canonical V1 cases: inventory zero
validation, missing-product status, order quantity totals, missing-inventory
runtime failure, order response schema, valid order-creation status, and a report
with no endpoint. It uses temporary SQLite fixtures, canonical setup requests,
in-process Sandbox HTTP, real tools and the agent loop with a deterministic
decision provider. Each case runs twice after reset to check state isolation.
The recorder and deterministic evaluators check tool arguments and evidence
links, including HTTP failure/request-ID/log correlation. Negative probes cover
invalid arguments, forbidden endpoints and missing logs; an unrelated request ID
must fail the log-argument evaluator.

Network connections are blocked in this module; no API key, OpenAI call, judge
service or Phoenix instance is required. Scripted final diagnoses check structured
application acceptance after runtime assertions, not model reasoning or semantic
answer quality. In particular, scripted acceptance of valid `NO_BUG_FOUND` does
not establish live provider quality or explain the historical V1/V2 rejection
subtype. Both no-bug cases passed the separate current-system V3 run; the
historical failures remain recorded. Live-evaluation observations are not
deterministic test scores. Regression gates and live evaluation have separate
responsibilities as described above.

### Maintaining Golden coverage

1. Start from a concrete failure or changed contract. Reuse canonical cases via
   `load_evaluation_cases` and existing Sandbox initialization/reset/setup; do not
   copy case definitions or edit the frozen V1 dataset. A case outside that frozen
   set needs a separately reviewed dataset/version decision.
2. Assert independent observations: real HTTP status/body, documented schema,
   persisted state, validation failures, or request-ID/log relationships. Inject
   deterministic providers only where needed; a canned diagnosis or judge label
   followed by an equality assertion does not prove correctness. Do not require
   one tool order when several investigations are valid.
3. Include relevant failure boundaries and verify reset/session isolation. Preserve
   the deliberately seeded bugs unless a separately approved behavior change
   intentionally changes them. Review changed expectations against the contract;
   do not weaken assertions, drop cases or suppress failures to obtain green CI.
4. Run `python -m pytest -m golden`, then the full offline checks above. For a new
   material invariant, temporarily violate it in an isolated negative control and
   confirm the assertion fails; remove the mutation and rerun affected checks.
   Keep the final diff free of intentional failures and private evidence.
