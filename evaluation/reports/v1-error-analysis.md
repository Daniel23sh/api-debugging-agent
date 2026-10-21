# APILens Phase 9 — V1 error analysis

Analysis date: 2026-10-21. **Recommendation:** make one controlled, prompt-level
change in Phase 10: direct investigation toward approved runtime reproduction when
a suspected cause depends on unobserved state, using correlated logs when needed
to explain a reproduced server failure. Do not implement that change in Phase 9.

The five unsuccessful investigations separate into three cases with an unresolved
runtime-evidence gap, all paraphrases of one bug, and two cases with failed model
decision acceptance across two endpoint scenarios. Their common `INCONCLUSIVE`
status hides different mechanisms. The one `RESOLVED` failure guessed the correct
mechanism without establishing its runtime trigger.

## 1. Scope and frozen provenance

This report investigates the frozen [V1 results](v1-baseline-2026-10-18.md) and
[saved aggregate](v1-baseline-2026-10-18.json), following the Phase 9 requirements
in the local project specification, technical design, implementation plan, and
observability/evaluation document under `docs/`. It changes documentation only.
The agent, prompts, provider, tools, limits, dataset, fixtures, judges, evaluator
versions, metric semantics, and original private artifacts remain unchanged.

| Frozen attribute | Value |
|---|---|
| Experiment | `64a16dc9-5331-4240-9ca9-04371905d4d0` |
| Mode / evaluation revision | `live` / `initial` |
| Saved report ID | `3afd2466-3ba3-4901-b7bd-08ec7818967f` |
| Recorded Git parent | `6146a293bcdab71a21cf93c18fdbfcda9465c3ad` |
| Source/config SHA-256 | `5e4f455833b30a8ad4e6e8bcd51d3ed23ffb25368d4d14190f249f2d8c859dc5` |
| Dataset SHA-256 | `ea0d431c9ff6dc23aa914b4ead6efc8403d931e04a0576de400c111502eb5c0a` |
| Fixture / cases | `sandbox-v1` / 18 |
| Agent / reasoning / judge | `gpt-6-sol` / `medium` / `gpt-6.1-sol` |
| Limits | 8 decisions, 6 tool calls, 2 identical calls, 45 seconds |
| Evaluator versions | grounding and response quality: 2; other families: 1 |

The starting working tree was clean. `origin/main` was fetched, local `main`
was already current at `518ea5c`, and `phase-9-error-analysis` was created from it.
The current source digest is
`a4e3df98a86e32d74f8749a1d38218f62fd289ce89954908ff0cee5254ae9164`,
which differs from collection. The original run included reviewed, uncommitted
Phase 8 implementation; its Git parent alone is not its exact source identity.
No attempt was made to replace the historical checksum with the current one.

## 2. Evidence availability and method

**The evidence access gate passed.** The original ignored directory
`evaluation/artifacts/baseline/v1-2026-10-18-01/` contains all 18 run snapshots,
execution records, initial evaluator bundles with explanations, start markers,
manifest/evaluation metadata, saved report, and historical Phoenix checks.

Before causal analysis, the coordinator used the existing read-only
`load_archived_report` path documented in [BASELINE.md](../BASELINE.md#historical-read-only-access).
It validates dataset identity/order, historical comparison context, model identity,
snapshot checksums, experiment/case/session/trace/span bindings, evaluation start
bindings and judge metadata, saved result labels/versions/metrics, and aggregate
arithmetic. It preserves historical measurements; it does not run current judges,
recompute success eligibility, or require the current source digest to equal the
old one. The stricter execution/resumption provenance checks were not bypassed.
Every saved measurement and case row matches the committed aggregate. The public
copy additionally contains Phoenix/execution-policy metadata and edited limitation
prose; these are presentation differences, not changed measurements.

After that validation, the investigators read the original JSON records directly.
They distinguished decisions, actual tool attempts, observations available before
each decision, model corrections, application-generated feedback, and final
diagnoses. Saved evaluator explanations were examined as fallible measurements,
not authoritative causal labels. No case was executed, rejudged, or modified.

The Phoenix HTTP endpoint at `127.0.0.1:6006` was unavailable during this analysis.
However, the original project and trace records were accessible in the retained
`evaluation/artifacts/phoenix-phase8/phoenix.db`, opened with SQLite
`mode=ro&immutable=1`. No server was started and no storage was changed. Queries
were restricted to project
`apilens-v1-64a16dc9-5331-4240-9ca9-04371905d4d0` and the saved session trace IDs.
All 18 exact roots, session/case/status bindings, connected span trees, recorded
decision/tool spans, and LLM-child counts per decision matched the snapshots:
233 investigation spans, including 73 agent LLM spans. Judge traces were excluded.
The existing [baseline trace links](v1-baseline-2026-10-18.md#per-case-results-and-verified-phoenix-links)
are historical references requiring the local server; HTTP access is not claimed.

Two parallel read-only agents separately investigated the missing-inventory family
and the no-bug cases. The coordinator reconciled them, inspected representative
successful controls, and requested a fresh read-only review of the draft.
Relevant current agent/tool/evaluator code was read to explain recorded behavior,
never used to reinterpret saved judgments. Git comparisons show no changes between
the recorded parent and current `main` in the agent, Sandbox, or core evaluator
files examined. This corroborates those code-path explanations, without claiming
to reconstruct the exact uncommitted collection checkout.

Private evidence locators used below are relative to the original directory:
`cases/<case_id>/run.json`, its `started.json` and `execution.json`, and
`evaluations/initial/<case_id>/result.json` and `started.json`, with shared
`evaluations/initial/metadata.json`. These are local audit references, not public
attachments. Summaries below paraphrase findings; raw observations, source/log
snippets, provider bodies, and judge explanations are not reproduced.

## 3. Outcome summary

All 18 executions returned diagnoses and all 18 evaluator bundles completed.
There were 13 strict successes: 12 bug cases and the expected inconclusive control.
There were 14 status matches and four mismatches. Actual statuses were 13
`RESOLVED` and five `INCONCLUSIVE`. No execution exception or evaluator error
explains the five unsuccessful investigations.

| Case | Expected → actual | Root / grounding / quality | Principal finding |
|---|---|---|---|
| `order_missing_inventory_01` | RESOLVED → INCONCLUSIVE | INCORRECT / GROUNDED / GOOD | Runtime trigger left untested; deliberate cautious stop |
| `order_missing_inventory_02` | RESOLVED → RESOLVED | CORRECT / UNGROUNDED / GOOD | Same evidence gap; conditional mechanism presented as resolved |
| `order_missing_inventory_03` | RESOLVED → INCONCLUSIVE | INCORRECT / GROUNDED / GOOD | Runtime trigger left untested; deliberate cautious stop |
| `order_create_status_no_bug` | NO_BUG_FOUND → INCONCLUSIVE | NOT_APPLICABLE / GROUNDED / POOR | Decision rejected after correction; application fallback |
| `inventory_update_status_no_bug` | NO_BUG_FOUND → INCONCLUSIVE | NOT_APPLICABLE / GROUNDED / POOR | Decision rejected after correction; application fallback |

`INCORRECT` root-cause results for the two cautious bug diagnoses mean that no root
cause was returned, not that the agent asserted a contradictory explanation.
`GROUNDED` and `GOOD` do not establish successful investigation. A tool's
`success=true` means it produced an observation; it does not establish correct API
behavior or reproduction of the reported failure.

### Case/trace reference index

Identifiers already published in the baseline aggregate are retained for lookup.
Decision/observation/tool IDs below are local to each session.

| Case | Session ID | Trace ID | Root span ID |
|---|---|---|---|
| `order_missing_inventory_01` | `c44cdabd-2dfb-4029-a1f5-ec0140f43885` | `a639709e6015af502e9f127d3ef8a035` | `606e04039d4ed250` |
| `order_missing_inventory_02` | `efdeee61-e72b-4691-a9c5-d95b75fe842c` | `bec1dd67f9beb1d0429d79221934ff06` | `04584aeebaf52188` |
| `order_missing_inventory_03` | `d4be7407-5668-465b-9475-93c8426df58f` | `09273a0073144cd72688a533ba1d34b0` | `907b0f90f93264ed` |
| `order_create_status_no_bug` | `494f0479-a3a7-4a80-a733-b56630b12262` | `4c3db407ad0e8d3607aaea1f0ae1789f` | `895862d34f732c41` |
| `inventory_update_status_no_bug` | `2a184d52-e97c-4f99-9d9c-c95f81d29a26` | `161cc912e385c9138b00a041efaf7631` | `ed96d23f3b570535` |

## 4. Missing-inventory causal findings

### Shared sequence and available knowledge

All three snapshots record the following first five decisions. At each step,
`decision-N` produces `tool-N`, linked to `observation-N`; each later decision
has the ordered preceding observations available.

| Decision / tool / observation | Recorded action and evidential consequence |
|---|---|
| 1 | Inspect the `POST /orders` contract; relevant request/response contract is available. |
| 2 | Inspect its implementation; the missing-inventory dereference is visible, but the actual presence of an inventory row is not. |
| 3 | Inspect the product GET contract; this is a different endpoint, not a repeated orders-contract read. |
| 4 | Execute `GET /products/4`; HTTP 200 establishes product existence, not inventory existence or the order failure. |
| 5 | Inspect a nonexistent inventory GET contract; `ENDPOINT_NOT_ALLOWED` leaves the inventory-state question unanswered. |

There is **no recorded `POST /orders` attempt and no log inspection** in any of
these three investigations. The only HTTP observation is a successful product
lookup. No runtime exception/traceback was collected by the agent. The implementation
supports a conditional mechanism: a missing inventory row would be dereferenced
without a null check and is not handled by the route's existing error mapping.
It does not independently establish that this row is absent in the observed case.
The dataset's expected trigger is analyst ground truth, not evidence delivered to
the model. Its canonical reproduction request must not be credited as an action
the agent actually performed.

**Earliest definite error:** the invalid method/endpoint combination at
`decision-5` is a tool-arguments failure. The tool correctly rejects it; this is
not an infrastructure outage or evidence that inventory access is universally
impossible. Product inspection at decisions 3–4 can legitimately test an existence
hypothesis. The more consequential investigation failure is leaving the runtime
trigger untested through the final decision, despite an approved order-execution
tool being available. We cannot identify a hidden intention or prove that a
particular earlier diversion caused the final stop.

### `order_missing_inventory_01`

`decision-6` is an accepted final answer with observations 1–5 available. It
deliberately chooses `INCONCLUSIVE`, describes the candidate mechanism conditionally,
and identifies missing inventory-state or failure-trace evidence. It has five tool
attempts, six decisions, six model calls, and a verified 19-span trace.

The uncertainty is honest; the investigation is incomplete. One tool slot and two
decision slots remained, and no timeout, model rejection, or limit feedback was
recorded. The invalid GET specification request explains a failed evidence path,
but does not prove that it alone caused the stop. An order reproduction could
have created a relevant observation and request ID; whether the agent would then
finish correctly is untested.

Saved results classify the absent root cause as `INCORRECT`, the cautious claims
as `GROUNDED`, and usefulness/clarity as `GOOD`. These labels are consistent with
the observed stopping boundary, not evidence of task completion. Alternative:
the agent may have interpreted the prompt's prohibition on changing system state
as excluding POST requests. That is plausible, not demonstrated. **Classification:**
tool arguments, incomplete tool choice/trajectory, and downstream missing root
cause; high confidence in the observed gap and voluntary stop, moderate confidence
in the proposed recovery benefit, underlying reason for avoiding POST
`UNDETERMINED`.

### `order_missing_inventory_02`

After the common five steps, `decision-6` / `tool-6` / `observation-6` inspect
the product GET implementation. That confirms the product lookup path, not the
missing inventory row. `decision-7` is an accepted `RESOLVED` answer. It supplies
the expected conditional null-check mechanism while acknowledging the missing
runtime confirmation. Counts: seven decisions, six tools, seven model calls;
22-span verified trace.

The first definite error remains `decision-5`; the additional finalization error
is at `decision-7`, where an unverified trigger is promoted to a resolved incident
cause. The saved `CORRECT` root-cause judgment compares mechanism against expected
truth; the `UNGROUNDED` judgment compares claims against gathered observations.
They measure different things and can both apply. `GOOD` measures response quality
independently of truth and grounding.
There is no fabricated POST execution in the recorded diagnosis; the problem is
insufficient support for resolving the incident.

Alternative: code plus the user's reported 500 makes the cause a reasonable
hypothesis. It still leaves competing runtime causes unexcluded, as the diagnosis
itself admits. Six tool slots were consumed, but no limit rejection or forced
termination occurred. **Classification:** shared evidence-acquisition gap and
tool-argument error, followed by a grounding/finalization failure; high confidence.
The necessity of every preceding inspection and the cause of the model's
overconfidence remain uncertain.

### `order_missing_inventory_03`

The six tool actions match `_02`, but `decision-7` returns an accepted
`INCONCLUSIVE` diagnosis that preserves uncertainty about inventory state and the
actual exception. Counts: seven decisions, six tools, seven model calls;
22-span verified trace. Even though this paraphrase supplies an explicit order
payload, no order request was executed.

As in `_01`, the diagnosis accurately describes the evidence gap; the failure is
not an unsupported claim. The saved labels are `INCORRECT` root cause because
none is returned, `GROUNDED`, and `GOOD`. Consuming six tools made further evidence
collection harder, but the snapshot contains no attempted seventh tool, limit
feedback, timeout, or application-generated `LIMIT_REACHED`. Budget consumption
is a contributing opportunity cost, not a demonstrated enforced stop.

Alternative: restrictions on inventory GET may appear to prevent verification.
They prevent that particular lookup, while the approved reported-operation path
remains a candidate. **Classification:** tool arguments and incomplete tool
choice/trajectory, with downstream missing root cause; high confidence in the
recorded gap, unknown internal reason for the chosen path.

### Evaluator caveats across the family

Saved selection labels mark `decision-3` unnecessary in all three cases, and
decisions 5–6 unnecessary in `_02`/`_03`; `_01` decision 5 is appropriate. Some
explanations describe different endpoint inspections as repeats. The recorded
arguments do not support that repetition claim. There are zero exact duplicate
calls. Semantic fallback selection judgments see tool purpose and prior evidence,
not the current arguments; their explanations cannot establish the actual target.

Conversely, the first-HTTP deterministic heuristic marks `decision-4` appropriate
even though it is a product lookup rather than reproduction of the order failure.
Tool-argument results correctly mark decision 5 incorrect, while approved
noncanonical exploratory calls can remain `UNDETERMINED`. Preserve these saved
measurements; neither a favorable label nor an unnecessary label establishes
causation. No alternate valid sequence is rejected merely for differing from a
canonical dataset path.

## 5. No-bug causal findings

### `order_create_status_no_bug`

`decision-1` / `tool-1` / `observation-1` inspect the order contract, which
documents 201 for successful creation, matching the reported behavior.
`decision-2` / `tool-2` / `observation-2` inspect the order implementation,
which corroborates that status. Both calls have correct arguments.

At `decision-3`, both observations are available. `model-3` and corrective
`model-4` return through the SDK wrapper without recorded exceptions, but no
acceptable action is recorded. The provider emits decision-scoped
`MODEL_OUTPUT_VALIDATION_ERROR`; `observation-3` is internal provider feedback,
with no corresponding `tool-3`. The loop constructs the `INCONCLUSIVE` fallback.
It contains neither endpoint evidence nor a useful explanation of the status
question. Counts: three decision attempts, two tools, four model calls;
11-span verified trace. The root's accepted-decision counter is two, consistent
with the rejected third attempt; this is not missing recorder state.

**First consequential failure:** decision acceptance at decision 3, including the
failed correction. The status mismatch and poor response follow from that error
handling path, rather than a recorded model decision to withhold a conclusion.
The contract was available; no HTTP reproduction occurred, so actual successful
creation was not independently observed in this investigation. Saved judgments
are root cause `NOT_APPLICABLE`, grounding `GROUNDED` for accurately reporting
the internal failure, and response quality `POOR`. **Classification:**
provider/structured-output failure, with downstream response-clarity/usefulness
failure; high confidence at that boundary, rejection subtype `UNDETERMINED`.

### `inventory_update_status_no_bug`

The corresponding IDs have the same sequence: decision/tool/observation 1 inspect
the PATCH inventory contract, which documents 200 with an inventory object;
decision/tool/observation 2 inspect its implementation, corroborating that response.
The reported expectation of 204 is not the documented contract. At decision 3,
model calls 3–4 fail application acceptance after SDK return, followed by the
same provider error, internal observation 3, and generated fallback.

Counts and saved labels match the preceding case: three decision attempts, two
tools, four model calls, 11 trace spans; `NOT_APPLICABLE` / `GROUNDED` / `POOR`.
Neither execution nor logs were requested. **Classification and confidence:**
the same demonstrated provider/structured-output boundary, followed by an
unhelpful fallback; high confidence, exact rejection subtype `UNDETERMINED`.

### Competing explanations and control

Both failed decisions have two child LLM spans marked OK, while the decision
span records the validation error. Successful SDK calls do not imply valid
application decisions. The provider checks completion, action count/type,
structured diagnosis availability, decoded arguments, and status semantics.
The generic correction/error does not preserve which check failed. Raw provider
input/output is redacted. It is unsupported to assert a specific invalid field,
`NO_BUG_FOUND` semantic mismatch, intended final status, or model misunderstanding
of the API contract. SDK/network exceptions, limit exhaustion, endpoint denial,
context truncation, and state corruption are not demonstrated causes here.

The saved selection judges call implementation inspection at decision 2
unnecessary because the contract already answers the reported expectation.
Corroborating implementation is nevertheless a defensible path. No evidence
establishes that the extra inspection caused the later rejection.

The correctly inconclusive `insufficient_report_no_endpoint` is different:
`decision-1` is an accepted final answer identifying missing investigation inputs
and an actionable next step, with no observations, tools, or errors. Its trace
`6793fecdf0c7c70f1d85624f124cc72a` has four verified spans and one model call.
This control supports distinguishing legitimate uncertainty from fallback status.

## 6. Cross-case taxonomy and frequency

Primary mechanisms partition the five failures; secondary rows overlap and must
not be added as independent failures. Frequencies describe this run only.

| Taxonomy / mechanism | Cases | Distinct scope | Causal role |
|---|---:|---|---|
| Tool choice / incomplete trajectory: runtime trigger left unverified | 3/18 | One missing-inventory bug family, three paraphrases | Primary investigation gap; final uncertainty or unsupported resolution follows |
| Provider / structured-output acceptance failure after correction | 2/18 | Two no-bug endpoint scenarios | Primary terminal failure; exact subtype unknown |
| Tool arguments: unsupported inventory GET specification request | 3/18 | Same missing-inventory family | First definite erroneous action; not established as sole cause of final failure |
| Grounding / evidence interpretation: resolve an unverified trigger | 1/18 | Missing-inventory `_02` | Additional finalization failure |
| Root cause absent | 2/18 | Missing-inventory `_01`, `_03` | Downstream result of honest unresolved uncertainty |
| Response clarity/usefulness: internal-error-only fallback | 2/18 | Both no-bug cases | Downstream provider-error handling outcome |
| Unnecessary trajectory, saved evaluator labels | 11/53 tool attempts in 7/18 cases | Three missing-inventory, two no-bug, two successful product cases | Secondary measurements; some explanations are contestable |
| Execution/infrastructure or state failure | 0 demonstrated among five | No supporting incident | Not assigned as a cause |

The three `ENDPOINT_NOT_ALLOWED` events belong exactly to missing-inventory
decision/tool 5. Both `MODEL_OUTPUT_VALIDATION_ERROR` events belong exactly to
no-bug decision 3. These are five recorded error events, not a complete census of
causal mistakes. No failed case was application-terminated by a limit. There
are zero timeout retries and zero duplicate calls in the saved trajectories.

## 7. Successful controls and secondary weaknesses

`product_missing_status_01` combines contract, HTTP response, and implementation
before resolving the wrong-status behavior. `_02` and `_03` add a log lookup
after the observed response; saved judges call those two steps unnecessary.
They still reach correct, grounded diagnoses. This is a small efficiency
opportunity, not evidence that log inspection is intrinsically wrong. Their
observed convergence ratios are approximately 0.777778 versus 1 for `_01`.

`inventory_validation_zero_01` and `order_total_quantity_01` succeed using contract
and implementation without runtime execution: the inspected code directly
establishes their validation/calculation mechanisms. `order_response_schema_01`
also checks HTTP output. These controls rule out a blanket requirement to run
every endpoint or follow one fixed sequence. The material missing-inventory
difference is a runtime data condition that the inspected code alone does not
establish. Across V1, all nine agent HTTP attempts were GETs; this is an observation,
not proof that the provider could not issue POST/PATCH calls.

The prompt's general instruction against modifying system state could discourage
Sandbox mutation requests even though the execution tool permits them. This is
a plausible competing explanation for the observed policy, not an established
internal cause. Future prompt scope should be explicit without widening tools or
permissions. A convergence ratio of 1 also cannot establish optimality: common
omissions or waste can survive across all paraphrases.

## 8. Candidate improvement comparison

| Candidate | Affected evidence / strength | Impact and scope | Regression risk / measurement |
|---|---|---|---|
| **Choose runtime evidence when a cause depends on unobserved state** | 3 failed paraphrases, one family; direct trajectory/final-diagnosis support | Close the common evidence gap before either stopping or resolving; one focused prompt change | Extra mutations, tools, tokens, or latency if applied too broadly; measure all three cases plus existing success/grounding/trajectory/system metrics |
| Improve provider acceptance/correction | 2 failed cases, two scenarios; boundary certain, rejection subtype unknown | Could recover useful no-bug answers; precise corrective implementation not yet supported | Broad acceptance changes may admit invalid diagnoses; monitor both no-bug cases and all statuses/grounding; more discriminating evidence needed to select a specific fix |
| Improve endpoint-argument guidance | 3 denied calls, one family; clear invalid arguments | Remove one failed action per affected case; does not establish missing inventory state | May change exploration without improving strict success; measure incorrect arguments and remaining evidence gap |
| Tighten resolved-claim evidence threshold | 1 failed case; clear finalization gap | Prevent unsupported resolution; alone may only convert `_02` to inconclusive | Can suppress valid static diagnoses; grounding improves without necessarily increasing strict success |
| Remove inspections labeled unnecessary / increase limits | Some contested labels; no enforced limit failure | Efficiency gains possible; extra capacity alone does not select useful evidence | Risks useful corroboration or adds cost; weak causal fit as the principal V2 target |

The chosen target covers more failed case rows, but three paraphrases are not
three independent bug families. The competing provider class spans two endpoint
scenarios and remains important. Runtime-evidence selection wins here because its
missing information and a bounded way to seek it are identifiable; selecting a
specific provider fix would require guessing what was rejected. This is an
engineering priority based on causal clarity and scope, not a statistically proven
ranking of model weaknesses.

The target fits the current bounds: contract and implementation inspection,
reproduction, and correlated logs could use four tools and five decisions including
the final answer. This is a feasibility illustration, not an observed result or
a prescribed tool order.

## 9. One V2 target and testable hypothesis

**Target:** a single investigation-prompt change instructing the agent to seek
approved Sandbox runtime evidence when a material causal claim depends on
unobserved state. The reported operation is a candidate for reproduction; after
a reproduced 5xx, request-correlated logs are useful when needed to explain the
exception. Clarify this within the existing Sandbox tool permissions. Keep static
resolution valid where the available evidence already establishes the mechanism.
Do not add mandatory tools, new endpoints, automatic execution, extra limits,
new schema/provider behavior, or case-specific answers.

**Hypothesis:** this guidance will cause the three `order_missing_inventory`
paraphrases to establish the missing runtime link before finalization, increasing
their strict successes while retaining grounding. The predicted improvement is
evidence acquisition followed by a supported `RESOLVED` result, not merely a
status change. This has not been tested and is not a promised three-case gain.
The precise originating reason for avoiding reproduction remains unknown; the
prompt change tests an intervention on the observed policy gap.

Use existing outcomes and metrics on the frozen 18 cases to judge that hypothesis:

- In `_01` and `_03`, status match and root correctness should improve while
  grounding remains `GROUNDED`.
- In `_02`, root correctness and status already match; grounding must improve
  from `UNGROUNDED` to `GROUNDED` with actual supporting observations.
- Across all 18 cases, inspect strict success (V1: 13), status matches (14),
  root-cause/grounding/quality labels, tool selection and argument results, and
  explicit unavailable/error states. Do not compensate for new failures with
  favorable clarity labels or reduced path length.
- Monitor the 12 successful bug runs, especially static-evidence controls, and
  the correctly inconclusive control for regressions. Monitor both no-bug cases
  separately; their provider failures are not the chosen target and no recovery
  is assumed.
- Compare model/tool calls, latency, tokens, unnecessary/duplicate attempts,
  structured errors, and successful-only convergence. The missing-inventory V1
  paths have 6/7/7 decisions and 5/6/6 tools; useful execution/logging may replace
  exploration rather than shorten every path. More runtime calls can alter
  Sandbox state or expose other seeded behavior, so fixture isolation and exact
  original setup must remain unchanged.

Keep model/settings, tool contracts/permissions, provider, limits, dataset,
fixture/reset behavior, evaluator definitions/versions, and metric semantics
fixed. Record any later prompt change as a new source identity. This defines one
candidate intervention and its measurements, not the Phase 10 implementation or
execution plan. No rerun or paid request is authorized or performed here.

## 10. Risks, review, verification, and recommendation

There is one original run per case, with only one missing-inventory bug family.
No causal counterfactual was executed, and no statistical reliability or expected
effect size is claimed. Raw provider output and hidden reasoning are unavailable;
the exact no-bug rejection reason and the reason for avoiding POST are unresolved.
Runtime reproduction/logging is a supported next evidence path, not a guarantee of
success or the only legitimate approach. The live Phoenix HTTP service remains
unavailable; retained trace trees were inspected directly instead.

The analysis does not promote fixture truth, canonical requests, or historical
preflight checks into evidence actually observed by the agent. Qualitative judge
labels remain unchanged despite identified limitations. No credentials, raw
snapshots, full tool observations, source/log snippets, or judge explanations are
included in this public report.

**Independent review:** a fresh read-only reviewer independently validated the
archive, inspected all five failed trajectories and saved explanations, checked
successful controls and relevant code, and verified the retained trace trees.
The reviewer found no material causal, comparability, or privacy issue. One minor
wording correction clarified that response quality includes usefulness and
consistency as well as clarity; the report now states its independence from truth
and grounding. The reviewer also checked that an illustrative evidence path fits
the existing limits. No major conclusion changed and no second review cycle was
needed.

**Verification:** read-only assertions passed for archive/public measurement
consistency; all 18 case identities, 13 strict successes, 14 status matches and
four mismatches; the exact five failure IDs; 71 decisions, 53 tool attempts and
73 model calls; both error-to-case/decision mappings; failure session/trace/root
references; and local Markdown link targets. The 18 retained trace trees passed
the identity, connectivity and recorded-span/count checks described above.
The current source digest remained unchanged, and the normal manifest loader
still rejected historical execution access with `SOURCE_MISMATCH`. SHA-256
comparisons found no changes to 102 original baseline and Phoenix database/WAL/SHM
files. `git diff --check` passed; a separate whitespace check included this new,
untracked report. No existing tracked file changed.

No Python files changed. Pytest/Ruff were not run for this documentation-only
change; the relevant historical-archive test fixtures collect mock sessions and
evaluate mock judgments, which were unnecessary under the no-new-execution
constraint. Validation used the existing read-only archive path and direct
assertions over the frozen records instead. No baseline, agent session, judge
evaluation, paid OpenAI request, commit, push, or merge was performed.

**Phase 9 acceptance:** met. The five failures have evidence-linked causal
findings, explicit uncertainty and competing explanations, a reconciled taxonomy,
successful controls, one bounded V2 hypothesis, and independent review. The
unavailable HTTP service did not block analysis because original snapshots,
bundles, and retained trace records were sufficient.

**Phase 10 recommendation:** consider the single prompt-level runtime-evidence
target above. Preserve the provider/structured-output issue as a separately
identified, unresolved class. Do not combine both improvements into the first
controlled comparison. Phase 9 stops at this evidence-based recommendation.
