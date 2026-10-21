# APILens Phase 10 — Controlled V1/V2 experiment

Analysis date: 2026-10-21. **Finding:** the observed V2 investigations support the
Phase 9 runtime-evidence hypothesis in the targeted bug family. Strict successes
increased from **13/18 to 15/18**, with no previously successful case becoming a
strict failure. This is evidence from one run per case, not an estimate of a
reliable population-level improvement. Two no-bug failures remain unchanged, and
one target case remains unsuccessful under the original grounding judgment.

## Objective and controlled conditions

The [Phase 9 analysis](v1-error-analysis.md#9-one-v2-target-and-testable-hypothesis)
selected one intervention: revise `SYSTEM_INSTRUCTIONS` so material causal claims
depending on unobserved runtime state prompt consideration of approved Sandbox
reproduction and useful request-correlated logs. The instructions also clarify
that approved Sandbox POST/PATCH requests may modify isolated Sandbox data.
Static diagnosis remains valid; no fixed tool sequence or mandatory reproduction
was introduced. Source edits and external/production actions remain prohibited.

This was the sole agent-behavior change. Provider parsing/correction, model and
reasoning settings, tool contracts, permissions, limits, fixtures, dataset,
evaluators and metric semantics were unchanged. Separate V2 identity, preflight,
archive compatibility and comparison safeguards are experiment infrastructure,
not additional agent interventions. See [the workflow](../BASELINE.md#phase-10--controlled-v2-preparation-and-later-execution)
and [the frozen V1 report](v1-baseline-2026-10-18.md).

| Identity | V1 | V2 |
|---|---|---|
| Experiment | `64a16dc9-5331-4240-9ca9-04371905d4d0` | `d8c60628-26f8-4c6e-a415-d6d74d20d161` |
| Original saved report | `3afd2466-3ba3-4901-b7bd-08ec7818967f` | `617379b3-4ed2-464d-9921-c3c84d2d29e0` |
| Exact source reference | `a41786621ff79e16624f5cbdf51878ee47a31d3e` | `e0d4e4f56fb11459d2d2474df6666d86e0bfb28a` |
| Source/config SHA-256 | `5e4f455833b30a8ad4e6e8bcd51d3ed23ffb25368d4d14190f249f2d8c859dc5` | `3e4b473ed9e43349d549e1631fd6a853e282edd813e748f2d6b9c197c02774d7` |

Both are live, initial-evaluation experiments using the same ordered 18 cases,
`sandbox-v1` fixture and isolated reset/setup semantics. Dataset SHA-256 is
`ea0d431c9ff6dc23aa914b4ead6efc8403d931e04a0576de400c111502eb5c0a`.
Both use agent `gpt-6-sol`, reasoning `medium`, judge `gpt-6.1-sol`, and limits of
8 decisions, 6 tools, 2 identical calls and 45 seconds. Grounding and response
quality evaluator versions are 2; the other four evaluator families are version 1.

Recorded runtime versions match: Python 3.13.11, OpenAI SDK 3.19.0, Pydantic
2.13.5, HTTPX 0.28.1, FastAPI 0.141.1, OpenTelemetry SDK 1.44.0 and Phoenix OTEL
0.17.1. Exact source references reproduce the recorded checksums; Git parents
alone do not, because collection included then-uncommitted work. Controlled
definitions match after excluding only the prompt intervention and allowing the
explicitly reviewed historical reader/reporting implementations. V2 preflight
binds the original V1 report checksum and identities. These checks passed;
recorded dependency equality is not proof of full transitive-package equality.

## Evidence and method

Three independent, concurrent read-only investigations examined target cases,
remaining failures, and integrity/metrics. The coordinator reconciled their
findings against original snapshots, decisions, tool attempts, observations,
initial evaluator bundles and saved reports. Historical `load_archived_report`
validation passed for both reports, preserving saved labels and validating their
snapshot/result bindings and aggregate arithmetic. Cohort and reference checks
passed without rerunning agents or judges.

The previously executed deterministic comparison's terminal output was not found
as a retained final artifact. V2's `comparison.json` is the preflight reference
binding, not final comparison output. This analysis independently checks the
saved inputs and arithmetic; it does not repeat the comparison command or claim
to have inspected an unavailable output file.

Both saved export-verification records contain 18/18 `VERIFIED` roots in separate
Phoenix projects. Investigators also inspected retained databases with immutable,
read-only SQLite access: all 18 V2 session trees matched their exact project,
trace/root bindings, connected parent chains and recorded model-call counts
(227 investigation spans, including 71 LLM spans). The three V1 target trees were
also checked against the V2 trees. These are retained trace checks, not a claim
that HTTP links remain available. V2 used an isolated copy of retained Phoenix
storage; original V1 storage was preserved. No server or storage was reset here.

Private evidence locators, relative to each experiment directory, are
`cases/<case_id>/run.json`, `execution.json`,
`evaluations/initial/<case_id>/result.json`, `reports/<report_id>/summary.json`
and `trace-checks/`. The directories are
`evaluation/artifacts/baseline/v1-2026-10-18-01/` and
`evaluation/artifacts/baseline/v2-2026-10-21-01/`. Only sanitized summaries and
lookup identifiers appear below; raw experimental content remains ignored.

## Results and denominators

Both experiments have 18 returned executions and 18 complete evaluator bundles.
There are no missing evaluator units, evaluator errors, interrupted executions or
missing system measurements. `RETURNED` includes application-generated fallback
diagnoses; it does not mean successful investigation.

Strict success requires expected final status and `GROUNDED`, plus `CORRECT`
root cause for bug cases. Response quality and tool-efficiency labels are separate
measurements. `PARTIALLY_GROUNDED` does not pass strict success.

| Saved outcome | V1 | V2 |
|---|---:|---:|
| Strict successes / all cases | 13/18 (72.2%) | 15/18 (83.3%) |
| Expected final status matches | 14/18 | 16/18 |
| Root cause `CORRECT` / scored bug cases | 13/15 | 15/15 |
| Root cause `NOT_APPLICABLE` | 3/18 | 3/18 |
| Grounding, all 18 scored | 17 GROUNDED; 1 UNGROUNDED | 17 GROUNDED; 1 PARTIALLY_GROUNDED |
| Response quality, all 18 scored | 16 GOOD; 2 POOR | 16 GOOD; 2 POOR |
| Actual final statuses | 13 RESOLVED; 5 INCONCLUSIVE | 15 RESOLVED; 3 INCONCLUSIVE |

The observed strict-success increase is two cases, or 11.1 percentage points.
The number labeled fully grounded is unchanged: `_02` improves from ungrounded,
while `_01` changes from a grounded inconclusive answer to a partially grounded
resolved answer. Correct root cause and matching status alone do not make `_01`
successful. Neither experiment returned `NO_BUG_FOUND`.

| Family / case | Cases | V1 strict | V2 strict | Interpretation |
|---|---:|---:|---:|---|
| `inventory_validation_zero` | 3 | 3 | 3 | Quality retained; more runtime calls |
| `product_missing_status` | 3 | 3 | 3 | Quality retained; two log calls removed |
| `order_total_quantity` | 3 | 3 | 3 | Quality retained; two added reproductions |
| `order_missing_inventory` | 3 | 0 | 2 | `_02` and `_03` become successful |
| `order_response_schema` | 3 | 3 | 3 | Strict outcomes retained |
| `order_create_status_no_bug` | 1 | 0 | 0 | Acceptance failure persists |
| `inventory_update_status_no_bug` | 1 | 0 | 0 | Acceptance failure persists |
| `insufficient_report_no_endpoint` | 1 | 1 | 1 | Legitimate inconclusive control retained |

All 13 V1 strict successes remain successful. This excludes strict-success
regressions, not every quality or efficiency regression.

## Target family: observed runtime-evidence behavior

V1's three target investigations never executed the reported POST or inspected
logs. Product lookup established product existence, not the runtime inventory
condition. Each also attempted an unsupported inventory contract lookup.
V1 `_01` and `_03` stopped inconclusively; `_02` resolved with an ungrounded
runtime-trigger claim. See the [case analysis](v1-error-analysis.md#4-missing-inventory-causal-findings).

All three V2 snapshots record the same accepted sequence:

| Decision / tool / observation | Recorded evidence |
|---|---|
| 1 | Inspect the reported `POST /orders` contract. |
| 2 | Execute `POST /orders`; observe HTTP 500 and a request ID. |
| 3 | Inspect logs using that same request ID; observe the corresponding failure. |
| 4 | Inspect the order implementation; connect the runtime exception to the missing-inventory dereference. |
| 5 | Return `RESOLVED` with the causal mechanism. No fifth tool call. |

The HTTP tool itself completed successfully while reporting a server failure;
its success flag is not proof of correct API behavior. The actual response,
correlated exception and inspected implementation supply the runtime link absent
in V1. Each V2 case uses 5 decisions, 4 tools and 5 model calls; V1 used
6/7/7 decisions, 5/6/6 tools and 6/7/7 model calls respectively. No target case
records validation failure, limit exhaustion, duplicate call or timeout retry.

| Case | V1 → V2 actual status | V2 root / grounding / strict result |
|---|---|---|
| `order_missing_inventory_01` | INCONCLUSIVE → RESOLVED | CORRECT / PARTIALLY_GROUNDED / unsuccessful |
| `order_missing_inventory_02` | RESOLVED → RESOLVED | CORRECT / GROUNDED / successful |
| `order_missing_inventory_03` | INCONCLUSIVE → RESOLVED | CORRECT / GROUNDED / successful |

For `_01`, the saved grounding explanation questions a minor request-parameter
detail rather than the central runtime cause. The full snapshot records that
detail in the submitted tool arguments. However, the grounding judge receives
the user report, diagnosis and collected observations, not recorded decision/tool
arguments; the response observation does not repeat that input detail. `_01`'s
user report also omits it, whereas `_02` and `_03` supply it. This evidence-input
boundary explains the differing support available to the judge. It does not
establish random judge inconsistency or justify replacing the saved label.
The original `_01` strict failure is retained without rejudging.

**Hypothesis assessment:** all three observed trajectories perform the selected
runtime-evidence behavior and establish the central cause. Two satisfy strict
success; the prediction of three fully grounded successes is not met. The
controlled intervention and observed evidence path support the hypothesis within
this family, while a single stochastic run cannot establish that the prompt alone
caused each change or guarantee repeatability. The prompt combines guidance and
Sandbox-write clarification, so their individual contributions are unseparated.

## Remaining failures: acceptance boundary, not deliberate uncertainty

In both versions, each no-bug case inspects its contract at decision 1 and
corroborating implementation at decision 2. These observations are identical
between V1 and V2 and establish the documented success-status behavior. No HTTP
execution or log inspection occurred; actual runtime behavior was not tested.

At decision 3, the saved accepted decision is absent. Model calls 3 and 4 record
usage and no request exception; the latter is the provider's one corrective
attempt. After neither response is accepted, `MODEL_OUTPUT_VALIDATION_ERROR`
produces internal observation 3 and the loop generates an `INCONCLUSIVE` fallback.
There is no tool 3. Each case has 3 decision attempts, 2 tools and 4 model calls.
Retained V2 traces show two LLM children under the failed third decision.

The fallback diagnoses are identical across versions. Their `GROUNDED` labels
reflect the recorded internal error, while `POOR` quality reflects the lack of a
useful domain answer. Root cause is `NOT_APPLICABLE`; expected `NO_BUG_FOUND`
does not match the fallback. These differ from the accepted, correctly
inconclusive missing-input control.

The provider validates several response-shape and diagnosis-semantic conditions.
The artifacts do not retain the rejected check, malformed field, intended final
status or raw model body. Successful request metadata and LLM spans do not prove
an acceptable application decision. A specific SDK defect, model contract
misunderstanding or invalid `NO_BUG_FOUND` field is therefore undetermined.
The extra implementation lookup is defensible corroboration despite being judged
unnecessary; no evidence shows that it caused rejection.

A credible future investigation would first distinguish sanitized acceptance
failure subtypes and test response translation with synthetic no-bug answer
shapes. No such instrumentation, provider change or second intervention is made
in Phase 10.

### Trace lookup references

These are actual saved trace IDs, corroborated by retained records; they are not
fabricated public Phoenix URLs. V1 references are also indexed in Phase 9.

| Case | V1 trace | V2 trace |
|---|---|---|
| `order_missing_inventory_01` | `a639709e6015af502e9f127d3ef8a035` | `fe210672a8d2668fb06e79e19e93b133` |
| `order_missing_inventory_02` | `bec1dd67f9beb1d0429d79221934ff06` | `618dde8c5db5cb7d9f7843f4aae7322e` |
| `order_missing_inventory_03` | `09273a0073144cd72688a533ba1d34b0` | `9c0b37297620c70c41895917013bdc04` |
| `order_create_status_no_bug` | `4c3db407ad0e8d3607aaea1f0ae1789f` | `dd4322d71b7a3e120139a56fc6c9f92b` |
| `inventory_update_status_no_bug` | `161cc912e385c9138b00a041efaf7631` | `dabd5878b3ac11bad24d00ad20b1c180` |

## Tool use, tokens and latency

| Tool-evaluation measure | V1 | V2 |
|---|---:|---:|
| Selection: APPROPRIATE / scored | 42/53 | 49/51 |
| Selection: UNNECESSARY / scored | 11/53 | 2/51 |
| Selection: NOT_APPLICABLE; UNDETERMINED | 16; 2 | 16; 2 |
| Arguments: CORRECT / scored | 42/45 | 51/51 |
| Arguments: INCORRECT / scored | 3/45 | 0/51 |
| Arguments: NOT_APPLICABLE; UNDETERMINED | 16; 10 | 16; 2 |

These evaluators operate over decision attempts: 71 V1 and 69 V2 expected units
each. Final-answer decisions account for non-applicable units; the two rejected
decisions remain undetermined. Argument accuracy is not a rate over all tools
when other argument units are undetermined. Unnecessary-call labels are fallible
judgments, not proof that an alternative valid investigation was wrong.

| Agent-session system measure (all 18 observed) | V1 | V2 |
|---|---:|---:|
| Decisions / tools / model calls | 71 / 53 / 73 | 69 / 51 / 71 |
| Input / output tokens | 111,387 / 7,425 | 105,708 / 7,414 |
| Total tokens | 118,812 | 113,122 |
| Mean session latency | 8.614 s | 9.629 s |
| Sum of session latencies | 155.056 s | 173.314 s |
| ENDPOINT_NOT_ALLOWED events | 3 | 0 |
| MODEL_OUTPUT_VALIDATION_ERROR events | 2 | 2 |
| Duplicate calls / timeout retries | 0 / 0 | 0 / 0 |

Total tokens fell by 5,690 (4.8%), while mean latency increased by 1.014 seconds
(11.8%). Fewer calls did not make the entire experiment faster. These measures
cover agent sessions, not judge token usage, monetary costs or end-to-end
experiment duration. No monetary saving is inferred.

The target family used 17 → 12 tools, 20 → 15 model calls, 44,329 → 26,134 tokens
and 43.853 → 35.426 summed seconds. Elsewhere, all three inventory-validation
cases added PATCH reproduction; two order-total cases added POST reproduction.
Those five previously successful cases used one extra tool/model call each with
no measured strict-quality gain. Product-status `_02` and `_03` removed log
inspection. Thus useful target-path changes coexist with broader runtime-call
expansion and slower non-target families; all reported selection labels for those
new calls remain appropriate rather than unnecessary.

Convergence remains a successful-only, within-experiment measure. V2 target
`_02`/`_03` both score 1; `_01` is excluded by grounding. V2 order-total `_01`/`_02`
score 5/7 versus `_03`'s 1; all three inventory-validation paths score 1 despite
each being longer than V1. Ratios of 1 do not prove optimality, and changing
eligible cohorts prevents treating a pooled convergence change as quality gain.

## Limitations and Phase 10 conclusion

There is one run per case and one target bug family with three correlated
paraphrases. The gain is not three independent bug-family discoveries. LLM agent
and judge variability, hosted-model changes between dates and local timing noise
remain possible confounds despite matching recorded settings. Qualitative labels
are measurements, not ground truth; `_01` illustrates a narrower judge evidence
view than the full recorder. Raw provider output is unavailable, leaving the
repeated acceptance subtype unresolved. Recorded dependency versions do not
reconstruct the complete historical transitive environment. The final comparison
terminal output is not retained locally, though its saved inputs are available
and independently validated.

Phase 10's controlled-comparison requirements are met: the selected intervention
was isolated, complete saved results were examined, and both gains and trade-offs
are documented. The observed runtime-evidence mechanism improved and two target
cases became strict successes without losing any prior strict successes. The
stronger prediction of three fully grounded target successes was not met. Retain
the 15/18 result, disclose `_01` and both no-bug failures, and treat provider
acceptance diagnostics as a separate future investigation. No further tuning,
new evaluation or Phase 11 work is part of this report.

**Verification:** read-only archive validation, cohort/source/reference checks,
case and trace identifiers, saved aggregate arithmetic and denominator checks
passed. All 197 original V1/V2 baseline files retained their SHA-256 hashes across
analysis. Local report links and whitespace were checked. Only this sanitized
report was added; no production or evaluation code changed. Pytest/Ruff were not
rerun for this documentation-only task. No agents, judges, paid requests, report
generation or comparison execution were repeated.

**Independent review:** a fresh read-only reviewer who did not author the report
independently checked both archives, exact-source cohort compatibility, all 36
status/strict-outcome bindings, aggregate denominators, target grounding evidence,
the no-bug acceptance boundary, saved export checks, privacy and planning
consistency. No material findings remained. The reviewer did not repeat the
investigators' retained SQLite tree examination; that evidence is attributed to
the investigations above. No experiment or judgment was rerun during review.
