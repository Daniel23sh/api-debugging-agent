# Application and demo verification — 24 October 2026

This is targeted current verification from `final-portfolio-readiness`, based on
`a295e7b` (PR #33). It is **not** a rerun or replacement of the frozen V1/V2
experiment. No historical dataset, judgment, snapshot, report or metric changed.

## Current acceptance defect and fix

Both historical no-bug reports were tested offline through the real OpenAI SDK
parser with mocked wire responses, real registered tools and disposable SQLite.
Valid `NO_BUG_FOUND` answers were accepted. Invalid cause/fix fields were rejected,
with exactly one corrective attempt and a safe fallback. This rules out a general
inability to translate a valid no-bug answer; it does not identify historical output.

Fresh live browser runs reproduced failures: order status returned HTTP 502;
inventory status did too. The Demo API deliberately exposes a safe error instead
of the `/debug` loop's internal `INCONCLUSIVE` fallback. Bounded private diagnostics
on inventory showed `stage=translation category=no_bug_cause_or_fix` on attempts
1 and 2. The output therefore reached application translation but violated the
existing rule that a no-bug diagnosis cannot contain a non-null cause or fix.
No raw output was retained, and the diagnostic does not distinguish which of those
two fields was populated. It does not prove the same subtype caused V1/V2 failures.

The cross-field Python validator was absent from the generated JSON Schema sent
to the model. The minimal fix describes the **existing** status semantics in the
provider's `_ModelDiagnosis` schema description. It does not loosen validation,
force a status, change system instructions, repair model output, add requests or
increase retries. Fixed rejection categories remain private server logs; no input,
provider text, validation body or exception details enter them or the HTTP response.

After rebuilding, both reports returned accepted `NO_BUG_FOUND`, null cause/fix
and supporting contract evidence. Order status also inspected implementation;
inventory status used only the contract. Neither fresh no-bug investigation
performed HTTP execution. Separate deterministic real Sandbox probes confirmed
POST `/orders` with product 1/quantity 1 returns 201, and PATCH `/inventory/1` with
quantity 1 returns 200 with quantity 1. These probes made no model calls.

## Live sessions: exactly 12, no judge calls

All were initiated through the actual React page and Vite proxy, against the
Compose Agent and its fresh in-process Demo Sandbox. Model `gpt-6-sol`, reasoning
`medium`; OpenAI SDK 3.19.0, Pydantic 2.13.5, FastAPI 0.141.1. Sessions 1–6
investigated the failure before the schema fix; 7–12 verify the fixed runtime.
See [the metadata ledger](live-verification.json) for timestamps, all IDs and metrics.

| Session(s) | Report | Observed result |
| --- | --- | --- |
| 1, 9 | Missing inventory | `RESOLVED`; spec → HTTP 500 → same-request logs → implementation. |
| 2, 10 | Wrong status | `RESOLVED`; HTTP 200 for missing 999 and the implementation's explicit special case. |
| 3, 11 | Incorrect total | `RESOLVED`; implementation establishes product 2 uses unit price instead of price × quantity. No HTTP probe was used in these runs. |
| 4, 12 | Response schema | `RESOLVED`; contract expects `product_id`, HTTP returns `productId`, manual JSONResponse bypasses response-model serialization. |
| 5 | Order success status, before fix | HTTP 502; no accepted diagnosis or trace ID returned. |
| 6 | Inventory success status, diagnostics only | HTTP 502; two fixed semantic rejection categories in private logs. |
| 7 | Order success status, fixed | `NO_BUG_FOUND`; 201 agrees with contract/implementation. |
| 8 | Inventory success status, fixed | `NO_BUG_FOUND`; 200 with an inventory object agrees with contract. |

The first eight successful bug diagnoses were manually checked against their
reported observations and the intentionally buggy code. No automated live judge
was used, so this table is not a new “strict success” evaluation score. Static
answers are identified as such; they are not claimed to have reproduced HTTP.

### The filmed investigation (session 9)

- Trace: [`4631295a53f64f928590a55fac426154`](http://127.0.0.1:6006/redirects/traces/4631295a53f64f928590a55fac426154).
- HTTP/log request: `159f8b18-aa7e-44c5-a99c-24f4db588be1`.
- Agent duration 9.986 seconds; four tool attempts, five model requests.
- Observed input/output tokens 8,487 / 474; total 8,961.
- Root span and selected HTTP tool span loaded in the actual Phoenix UI. The
  matching request ID appears in the API/log timeline and diagnosis evidence.
- Actual Phoenix 20.23.0 redirect response had no X-Frame-Options or CSP header;
  the real page rendered and was usable at 1440 × 900 and 1280 × 720. No headers,
  authentication, exporter timing or cross-origin protections were altered.

An ID alone is not ingestion proof. Only observed loaded views are claimed here;
other ledger trace IDs are correlation references unless opened separately.

## Browser verification

Actual headed Chromium was used, including recorded browser footage. Screenshots
were inspected, not just DOM state. At 1440 × 900, 1280 × 720, 1024 × 768,
390 × 844 and 320 × 700: navigation, expanded evidence/timeline and technical
details stayed accessible, with no horizontal page overflow. Desktop embedding
was usable; narrower screens provided the exact external link. Result text,
HTTP-500/probe-success distinction, metrics and correlation metadata were readable.

Verified explicit consent, locked report during execution, one submission per
run, completion focus, smooth section navigation, copy diagnosis via clipboard,
modal opening/close, Escape from the parent toolbar and focus restoration. No
Phoenix iframe loaded before opening it. The cross-origin Phoenix document was
not accessed by application JavaScript. Phoenix controls keyboard shortcuts while
focus is inside its frame; use the parent Close action or external view as needed.

History reopening, returning to an unfinished draft, and reload were exercised
with actual captured results; no extra request occurred. Reload reset consent.
The first long browser script passed those checks then timed out because QA
closed the history disclosure before looking for Clear history. A separate
no-paid-call browser check corrected the automation and verified default Cancel
focus, the dialog focus trap, Escape cancellation and confirmed deletion.

**Separate deterministic UI checks**, not live LLM quality evidence: simulated
502/503/network failures, empty `INCONCLUSIVE` and `LIMIT_REACHED` results, missing
metrics and no trace. They rendered safely at mobile width, without overflow or
automatic requests. The offline suite additionally covers blocked storage,
malformed responses, all statuses, stale results and duplicate prevention.
No live deadline/limit case was forced, and no live quality claim is made for one.

## Offline and operational checks

| Check | Result |
| --- | --- |
| `OPENAI_API_KEY= PHOENIX_COLLECTOR_ENDPOINT= EVAL_JUDGE_MODEL= PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest` | 634 passed, including Golden, after the final Python change. |
| `.venv/bin/ruff check .` | Passed. |
| `.venv/bin/python -m mypy` | Passed, existing nine-file scope. |
| `npm ci` (frontend) | Passed from the unchanged lockfile. |
| `npm run typecheck` (frontend) | Passed. |
| `npm test` (frontend) | 129 passed. |
| `npm run build` (frontend) | Passed. |
| `git diff --check` | Passed. |
| `docker compose config --quiet` | Passed. |
| Fresh `docker compose up -d --build` and rebuilt Agent after fix | Passed; all three services healthy. Existing volumes preserved. |
| Actual Vite proxy and live `/demo/debug` | Passed, no frontend key or backend CORS change. |
| MP4 full decode and actual Chromium playback/seek/end | Passed; 71 seconds, H.264/yuv420p, 1440 × 1000, fast-start MP4. |

All automated tests ran without credentials and without paid calls. The existing
GitHub Actions `checks` job is unchanged. No commit, push, PR or merge was made.

Fresh read-only independent review found no material defects in the provider
change, private diagnostics, regression tests or walkthrough. Its independent
48 provider/schema tests and Ruff passed. Final media/provenance received a
separate review after creation. See [capture provenance](README.md).

## Follow-up: full-dataset V3 verification — 25 October 2026

The [separate V3 report](../../evaluation/reports/v3-verification-2026-10-25.md)
records 18 fresh investigations and their initial evaluation: **17/18 strict
successes**, 18/18 expected status matches and 18/18 verified Phoenix roots.
Both no-bug cases pass, and all 15 V2 strict successes are retained. The remaining
partial-grounding label and historical-comparison caveats are preserved there.

That approved run used 72 agent requests and 71 judge requests. These are separate
from the 12 browser sessions above, which used no automated judge. V3 preparation
passed 644 backend tests after its runner tests were added; the 634-test result
above remains the dated browser/media verification record. The original media
and ledger remain unchanged and are not V3 capture footage. See the
[V3 protocol](../../evaluation/V3.md) for the final checks and independent review.

## Repository cleanup — 25 October 2026

The `final-ponytail-cleanup` review covered all 123 tracked files and 23 pending
first-party files present at its start, including code, tests, configuration,
dependency locks, documentation and presentation assets. Existing readiness/V3
work was preserved. No file, dependency or meaningful test was removed.

The small cleanup removed duplicate tool-name storage in the dispatch registry,
a duplicate Vite host flag, one unused CSS selector, repeated CSS declarations
and a redundant JSX fragment. Dispatch tests now explicitly check all four
recorded tool names. Native dialogs, validation, private recorders and historical
archive compatibility remain because they enforce real contracts.

Verification passed: 644 offline backend tests including Golden, 129 frontend
tests, frontend typecheck/build, Ruff, scoped Mypy, dependency consistency and
Compose validation. A fresh image ran without credentials or networking, using
only disposable SQLite state to verify HTTP 500 and correlated logs. Desktop and
mobile CSS fixtures rendered identically before/after; this was an offline
rendering check, not a new live model or Phoenix investigation. Fresh independent
read-only review found no material findings.

All 202 historical dataset/report/private-artifact hashes and original media
hashes remain unchanged. V3's recorded source hash identifies the **pre-cleanup**
code; its exact evaluated source was preserved in a private ignored archive.
The cleanup is covered by deterministic checks, not a new live quality score.
No paid calls, commits, pushes or changes to running services/volumes were made.

## Limits to disclose

These are targeted observations from one run per scenario after the fix, not a
reliability estimate. Schema guidance can still be ignored; strict semantic
validation and safe failures remain necessary. The exact historical no-bug subtype
remains unknown. Do not relabel the saved V1/V2 results.

Phoenix ingestion/retention and provider availability can fail independently.
A local trace link is not a portable hosted artifact; screenshots and the video
are the durable fallback. Browser history is unencrypted, origin-scoped and has
no time expiry. Only five records are retained. This remains a trusted local
Sandbox demo, without public deployment, production APIs or automatic code edits.
