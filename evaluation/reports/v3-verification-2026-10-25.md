# APILens V3 — current-system verification

Execution date: **2026-10-25**. **17/18 strict successes (94.4%)**, compared descriptively with the frozen V1 **13/18** and V2 **15/18**. All 18 executions returned, all 18 initial evaluator bundles completed, and all 18 exact `DebugSession` roots were verified in Phoenix. No investigation or judgment was repeated.

Both no-bug controls now return accepted `NO_BUG_FOUND`, are `GROUNDED` and `GOOD`, and pass strict success. All 15 V2 strict successes were retained. The remaining non-success is `order_missing_inventory_01`: correct root cause, `PARTIALLY_GROUNDED`. The expected insufficient-input `INCONCLUSIVE` control passes.

## Purpose and controlled conditions

This is a new measurement of the current system after the diagnosis-schema clarification, not a new controlled V1/V2 intervention. It does not overwrite historical results or prove that schema guidance alone caused the observed differences. There are other source changes since V2, hosted-model/time drift and sampling/judge variability.

| Identity | Recorded V3 value |
|---|---|
| Experiment | `9cb480ec-480f-42e9-b887-68ba64018de5` |
| Original saved initial report | `8ba1fd7d-a259-4ccf-a87a-48bde5844672` |
| Source/config SHA-256 | `c6d66fc8742bc6edde3c7787611ddeee4b2b594ed813f9598331a1a40bfc8522` |
| Git parent | `a295e7b7b24aa9e95efec16b7200bf444b10f3e3` |
| Dataset SHA-256 | `ea0d431c9ff6dc23aa914b4ead6efc8403d931e04a0576de400c111502eb5c0a` |
| Phoenix project | `apilens-v3-9cb480ec-480f-42e9-b887-68ba64018de5` |
| Exact-root verification record | `328d1e36-4e8e-4c25-a861-ec58514f3125.json` |
| Verified at (UTC) | `2026-10-25T12:07:06.782073+00:00` |

The source hash identifies the evaluated Python/config contents, including uncommitted readiness/V3 preparation work. The Git parent alone is not an exact source checkout. No Python/config source changed between preflight and completion.

All three manifests have the same ordered 18 cases, `sandbox-v1` fixtures, agent `gpt-6-sol` with reasoning `medium`, judge `gpt-6.1-sol`, and limits of 8 accepted decisions, 6 tool attempts, 2 identical calls and 45 seconds. Each case used fresh isolated SQLite and the established ASGI Sandbox path. Dataset, fixtures, evaluator definitions and metric formulas were unchanged. The agent received reports and observations, not evaluation answers.

Recorded runtime versions match V1/V2: Python 3.13.11, OpenAI SDK 3.19.0, Pydantic 2.13.5, HTTPX 0.28.1, FastAPI 0.141.1, OpenTelemetry SDK 1.44.0 and Phoenix OTEL 0.17.1. Matching package/version records do not pin every transitive dependency or hosted model behavior. Evaluator versions remain grounding/quality v2 and the other four families v1.

## Results and denominators

Strict success requires expected final status and full grounding, plus correct root cause for bug cases. Response quality and tool efficiency are separate. `PARTIALLY_GROUNDED` does not pass; the expected inconclusive control can pass.

| Saved measure | V1 | V2 | V3 |
|---|---:|---:|---:|
| Strict successes / all cases | 13/18 | 15/18 | 17/18 |
| Expected status matches | 14/18 | 16/18 | 18/18 |
| Correct root cause / scored bug cases | 13/15 | 15/15 | 15/15 |
| Fully grounded | 17/18 | 17/18 | 17/18 |
| Response quality GOOD | 16/18 | 16/18 | 18/18 |

V3 has 15 `RESOLVED`, 2 `NO_BUG_FOUND` and 1 `INCONCLUSIVE` outcomes. Root cause is `NOT_APPLICABLE` for all three non-bug cases. Grounding remains 17 `GROUNDED` and 1 `PARTIALLY_GROUNDED`, the same distribution as V2. There are no execution, model-acceptance or evaluator error events in V3.

The two gains over V2 are the no-bug controls; there are no lost strict successes. This is a two-case / 11.1-percentage-point observed increase, not a population reliability estimate. Matching statuses are 18/18, while strict success remains 17/18.

### Per-case results

Trace links below refer to the new V3 project and were verified by exact root identity. They depend on local Phoenix storage/retention; the saved verification record is the durable availability evidence.

| Case / V3 trace | V2 strict | V3 status | Root | Grounding | Quality | V3 strict |
|---|---|---|---|---|---|
| [inventory_validation_zero_01](http://127.0.0.1:6006/redirects/traces/1bc6b005b1b3eeb52445f49466d21578) | pass | RESOLVED | CORRECT | GROUNDED | GOOD | pass |
| [inventory_validation_zero_02](http://127.0.0.1:6006/redirects/traces/f940027dc203cb6b2cb8e1a1c37193cc) | pass | RESOLVED | CORRECT | GROUNDED | GOOD | pass |
| [inventory_validation_zero_03](http://127.0.0.1:6006/redirects/traces/fabdb87e9f042f10814feff5410a8edb) | pass | RESOLVED | CORRECT | GROUNDED | GOOD | pass |
| [product_missing_status_01](http://127.0.0.1:6006/redirects/traces/0ab37675e2b93f724b47ac995c9a3bdc) | pass | RESOLVED | CORRECT | GROUNDED | GOOD | pass |
| [product_missing_status_02](http://127.0.0.1:6006/redirects/traces/ddaa5d00cdbd69fe9f66e9243c323c1d) | pass | RESOLVED | CORRECT | GROUNDED | GOOD | pass |
| [product_missing_status_03](http://127.0.0.1:6006/redirects/traces/b297d8ea0d72a68b87e138b775eb2a98) | pass | RESOLVED | CORRECT | GROUNDED | GOOD | pass |
| [order_total_quantity_01](http://127.0.0.1:6006/redirects/traces/968b0d9ae97e76eaef7ac19d971d4878) | pass | RESOLVED | CORRECT | GROUNDED | GOOD | pass |
| [order_total_quantity_02](http://127.0.0.1:6006/redirects/traces/299d81063c1e9f0ef7ad1371a05065a8) | pass | RESOLVED | CORRECT | GROUNDED | GOOD | pass |
| [order_total_quantity_03](http://127.0.0.1:6006/redirects/traces/8b738d56897f6738580113d80bc513e7) | pass | RESOLVED | CORRECT | GROUNDED | GOOD | pass |
| [order_missing_inventory_01](http://127.0.0.1:6006/redirects/traces/e103d9d965a3d33b97636ee9c9c9ab4f) | fail | RESOLVED | CORRECT | PARTIALLY_GROUNDED | GOOD | fail |
| [order_missing_inventory_02](http://127.0.0.1:6006/redirects/traces/1b6d833feff010c5504d0c009535ab97) | pass | RESOLVED | CORRECT | GROUNDED | GOOD | pass |
| [order_missing_inventory_03](http://127.0.0.1:6006/redirects/traces/17fcdd12b5bcfb6f3d27adb5d67ad80c) | pass | RESOLVED | CORRECT | GROUNDED | GOOD | pass |
| [order_response_schema_01](http://127.0.0.1:6006/redirects/traces/c3b4a79a1ba55d33a7d44b8919f9a46f) | pass | RESOLVED | CORRECT | GROUNDED | GOOD | pass |
| [order_response_schema_02](http://127.0.0.1:6006/redirects/traces/c3c833b48f590f9d27274696765cd430) | pass | RESOLVED | CORRECT | GROUNDED | GOOD | pass |
| [order_response_schema_03](http://127.0.0.1:6006/redirects/traces/a15e3a4998dbd25bdd51398ce3bd9ddd) | pass | RESOLVED | CORRECT | GROUNDED | GOOD | pass |
| [order_create_status_no_bug](http://127.0.0.1:6006/redirects/traces/c8bdf425f3172050334a14dc7466251f) | fail | NO_BUG_FOUND | NOT_APPLICABLE | GROUNDED | GOOD | pass |
| [inventory_update_status_no_bug](http://127.0.0.1:6006/redirects/traces/6731eaa1f8c2e4985f53eb85c94ad8bc) | fail | NO_BUG_FOUND | NOT_APPLICABLE | GROUNDED | GOOD | pass |
| [insufficient_report_no_endpoint](http://127.0.0.1:6006/redirects/traces/a0a9140906eb51b2e627e5cf5fa36f1f) | pass | INCONCLUSIVE | NOT_APPLICABLE | GROUNDED | GOOD | pass |

## Focused evidence review

**Both no-bug cases:** the agent inspected the relevant contract and implementation and concluded that the reported success status/body agrees with them. Both diagnoses contain evidence and null cause/fix fields. They did not execute an HTTP probe. The independent preflight reproduced the fixtures, but fixture preparation is not agent evidence or an investigation step. Saved judges label both answers `GROUNDED` and `GOOD`; the prior provider-acceptance failure did not occur in this run.

**Remaining `order_missing_inventory_01` failure:** the agent reproduced HTTP 500, inspected same-request logs, and inspected implementation. Root correctness is `CORRECT` and quality `GOOD`. The grounding judge flags a minor reproduction-parameter detail absent from its supplied observation/report view. Manual inspection of the fuller retained tool-call arguments confirms that detail was actually used. The judge receives gathered observations but not a separate list of tool arguments, so its evidence view is narrower than the private execution snapshot. This is the same documented visibility issue as V2. It does not establish invented execution or justify silently overriding the saved label. Strict success remains false.

No evaluator was changed or rerun to address that disagreement. No new failures or regressions relative to V2 strict eligibility were found. This review distinguishes accepting a valid diagnosis from judging its evidence support.

## Tool use, latency and usage

| Agent-only system measure, 18/18 observed | V1 | V2 | V3 |
|---|---:|---:|---:|
| Decisions / tools / model requests | 71 / 53 / 73 | 69 / 51 / 71 | 72 / 54 / 72 |
| Input / output tokens | 111,387 / 7,425 | 105,708 / 7,414 | 114,554 / 6,389 |
| Total tokens | 118,812 | 113,122 | 120,943 |
| Mean session latency | 8.614 s | 9.629 s | 7.458 s |
| Sum of session latencies | 155.056 s | 173.314 s | 134.238 s |

Compared with V2, V3 used **6.9% more agent tokens**, one more model request and three more tools, while measured mean agent latency was **22.5% lower**. These are descriptive measurements, not proof of a speed improvement caused by the fix or a monetary-cost saving. Judge calls/tokens and fixture setup are excluded from agent-session metrics.

Tool selection has 54 scored units: 50 `APPROPRIATE`, 4 `UNNECESSARY`; 18 final decisions are not applicable. Tool arguments have 52 `CORRECT`, 2 `UNDETERMINED`, and 18 not applicable units, with no incorrect scored arguments. The two undetermined units are approved exploratory product spec/HTTP calls in `order_total_quantity_01`; they are not canonical case requests. Do not report 52/52 as correctness over all 54 tool attempts.

The four unnecessary-tool labels concern one validation-case log lookup, one order-total product-contract lookup, and the two no-bug implementation lookups. These are retained judgments, not proof that every corroborating alternative is wrong. No duplicate calls or timeout retries occurred. Successful-only convergence remains supplementary within the current source cohort; no V1/V2/V3 convergence pool is constructed.

### Actual approved paid scope

Exactly **18 fresh investigations** and **one initial evaluation revision** ran. The recorder reports **72 agent model requests**. There are **71 semantic judge units** (20 tool-selection, 15 root-cause, 18 grounding, 18 quality). A complete, unpaginated read of the isolated Phoenix project reconciles them with 72 child agent LLM spans and 71 root judge LLM spans: **143 observed model-request spans in total**. No output corrections or API retries added requests in this run. There was no fresh V1/V2 collection, rejudging, duplicate execution, extra demo call or tuning pass.

## Integrity, privacy and verification

All 18 case execution records are `RETURNED`; all 18 evaluator bundles are `COMPLETE`, with no missing units or evaluator errors. Every recorded trace/root ID matches a `DebugSession` root in the dedicated Phoenix project. The saved trace-check record contains 18/18 `VERIFIED`. The complete project has 306 spans including the synthetic preflight. All 143 LLM spans have redacted input/output values.

Historical reports were loaded through the read-only archive API without recomputing their judgments. SHA-256 checks confirmed all 202 original historical dataset/report/private-artifact files remained byte-identical. The public V3 JSON is a byte-identical copy of the original saved initial summary, containing only validated counts, labels, measurements and lookup metadata. Raw snapshots, tool arguments, observations and judge explanations remain ignored private artifacts.

Offline preparation already passed 644 backend tests, including Golden, plus 57 focused V3/live-runner/V2 tests and independent review. Source/config hashes are unchanged, so paid execution did not require repeating that suite. Final Ruff, scoped Mypy and diff/link/arithmetic/privacy checks passed. A fresh read-only result review found no material issues; it independently checked saved results, aggregate arithmetic, historical integrity, trace identities, public-summary privacy and comparison claims. The reviewer used the saved Phoenix verification evidence rather than repeating the live query. Frontend/API/agent/evaluator code was not changed during this measurement.

## Limits and interpretation

There is one run per case and several correlated paraphrases. Model/judge stochasticity, hosted-model changes, source evolution and local timing make historical differences non-causal. Identical model aliases/runtime version records do not remove those confounds. No confidence interval, dollar-cost estimate, production reliability guarantee or new controlled schema-only experiment is claimed.

The exact historical V1/V2 rejected-output subtype is still unknown. Fresh current diagnostics identified a semantic rejection before the schema clarification; V3 verifies current behavior, not historical root cause. The known grounding-view disagreement remains visible as the one strict non-success.

[Original saved V3 summary](v3-verification-2026-10-25.json) · [V3 protocol](../V3.md) · [Historical V1/V2 report](v1-v2-experiment-2026-10-21.md)
