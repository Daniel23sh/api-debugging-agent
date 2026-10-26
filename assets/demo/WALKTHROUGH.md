# APILens: interview and demonstration guide

## 45-second pitch

APILens investigates API bug reports with evidence rather than just generating
an explanation. A language model proposes one action at a time. Python code
validates that proposal, executes one of four bounded tools, and feeds the
observation back into an explicit session state. The tools inspect the API
contract, reproduce a request, inspect request-correlated logs, or read approved
implementation sections. The result is a structured diagnosis, not an applied
code change. The React demo shows the actual timeline and measured usage, and
opens the investigation's exact Phoenix trace. A frozen 18-case experiment
recorded 13 strict successes before a focused prompt change and 15 afterward.
A separate current-system verification recorded 17/18, including both no-bug
cases. These are small observed comparisons, not reliability guarantees. The interesting
engineering is the controlled execution boundary, correlation, privacy, and
separation of diagnosis correctness from evidence grounding.

## Before the interview

From the repository root, preserve the existing private `.env` and its backend
OpenAI key. Never project the file or terminal environment.

```bash
docker compose up --build -d --wait
docker compose ps
# Separate terminal, Node 22.12+:
cd frontend
npm ci
npm run dev
```

Open <http://127.0.0.1:5173> at 1440 × 900 or larger. Keep Phoenix on
<http://127.0.0.1:6006>. The embedded viewer requires at least 1280 × 720.
Health checks and page load are free; pressing **Start investigation** makes paid
model requests. Use the same hostname and port to retain browser-local history.
Have [the genuine recorded demo](apilens-demo.mp4) and
[the captured evidence](investigation-evidence.png) available offline.

## A practical 3–5-minute demo

| Time | Action and explanation |
| --- | --- |
| 0:00–0:40 | Deliver the pitch. Point out the four symptom categories and the cost acknowledgment. Select **Missing inventory**; read the report about product 4 and HTTP 500. |
| 0:40–1:15 | Explicitly acknowledge cost and start once. Explain that the UI waits for completion and invents no tool progress. The report is locked and duplicate submissions are prevented. Actual duration varies. |
| 1:15–2:00 | Read the returned status and cause. `RESOLVED` means a cause was identified; no fix was applied. Open Evidence. In the recorded run, HTTP reproduction and correlated logs share a request UUID, and implementation evidence explains the unchecked missing inventory row. Do not promise this exact sequence for a future run. |
| 2:00–2:45 | Open Timeline and its technical details. Show that a successful HTTP probe can observe HTTP 500. Compare HTTP/log request IDs. Explain that tool attempts, model requests, token usage, and decisions are different counts. |
| 2:45–3:30 | Open **Explore Full Trace**. Show the same investigation's `DebugSession`, decision/LLM children, tools, and final diagnosis. The displayed trace ID is OpenTelemetry correlation metadata. Phoenix ingestion is asynchronous. I/O is redacted; Phoenix is not a hidden-reasoning viewer. |
| 3:30–4:15 | Close the viewer. Copy Diagnosis. Return to the report, type a draft, and reopen the completed result from Recent. Explain that the five-entry history is unencrypted, local to this browser origin, and can be cleared. Return to the preserved draft without a request. |
| 4:15–5:00 | Show the controlled V1/V2 comparison (13/18 → 15/18), then the separate V3 verification (17/18). Both no-bug cases now pass; distinguish 18/18 status matches from strict success and explain the remaining partial-grounding case. Do not attribute the V3 difference solely to the schema clarification. |

If a diagnosis is inconclusive or limited, show its missing evidence honestly.
Never describe a saved record as a fresh live run.

## Follow one report through the code

1. **React** — `frontend/src/App.tsx` owns the draft, consent, in-flight guard,
   displayed snapshot and local history. `api.ts` makes one relative
   `POST /demo/debug` request through Vite and validates the response with Zod.
2. **FastAPI** — `agent_service/main.py` validates the issue/scenario. The demo
   path creates disposable fixtures, a memory-only recorder, and uses the same
   `run_debug_session` as `/debug`. Missing credentials fail safely before a call.
3. **Provider** — `agent_service/agent/openai_provider.py` translates between the
   Responses API and typed decisions. The strict JSON Schema describes existing
   status semantics. The application still validates them; schema guidance alone
   cannot guarantee truth. There is one corrective attempt for invalid output,
   and no SDK API-error retries. Only fixed rejection categories enter private logs.
4. **Loop and state** — `agent_service/agent/loop.py` enforces decisions, tool
   counts, repetitions and the session deadline. `state.py` records accepted
   decisions, actual attempts, observations and request IDs. Model corrections
   are provider requests within a decision, not extra accepted decisions.
5. **Dispatch/tools** — `dispatch.py` validates the registered name and Pydantic
   arguments before execution. `agent_service/tools/` controls methods/routes,
   timeouts, body size, UUID log lookup and allowlisted source sections. The
   model proposes actions; it does not get Python execution, shell or arbitrary URLs.
6. **Sandbox** — `sandbox_api/main.py`, `db.py`, `models.py` and `source_map.py`
   provide the intentionally faulty API, SQLite fixtures/logs and approved source
   mapping. The bugs remain deliberately unfixed for investigation.
7. **Result/trace** — `schemas.py` enforces status semantics. `demo.py` records
   ordered, sanitized metadata and measured metrics. The frontend renders strings
   as text. `observability/__init__.py` owns OpenTelemetry/OpenInference and
   privacy filtering. The root trace ID builds a fixed local Phoenix redirect.
8. **Evaluation** — `evaluation/dataset.py`, `recorder.py`, evaluators,
   `metrics.py` and orchestration separate frozen expected behavior, observed
   snapshots and judgments. They do not supply answers to the live Demo API.

## Concepts and tradeoffs worth explaining

- **FastAPI/Pydantic:** request/response models validate structure; custom
  validators express relationships such as “NO_BUG_FOUND has evidence but no
  cause/fix.” Such Python rules are not automatically expressible in generated
  JSON Schema, so the provider must describe them to the model as well.
- **Async execution:** waiting for HTTP/model I/O need not block the server.
  Bounded timeouts and cancellation limit application work; they do not prove
  that a remote provider stopped processing or that a charge was avoided.
- **SQLite isolation:** every demo request gets a fresh temporary file, seeded
  fixtures and an injected ASGI transport. The Sandbox runs in the Agent process;
  it does not use the Compose Sandbox network/database. `/debug` uses the regular
  network service/shared database. No global reset endpoint exists.
- **Correlation:** the Sandbox assigns an HTTP request UUID and writes events
  into the same request's SQLite database. Log inspection looks up that UUID.
  UUID syntax is validated; current-session provenance is intended, not enforced.
  An ID match links records; their contents establish the diagnosis support.
- **OpenTelemetry:** a root `DebugSession` contains decision, LLM, tool and final
  spans. A 32-digit trace ID is neither a request UUID nor authorization.
  Batch export means “ID exists” does not mean “Phoenix has ingested it.”
- **Browser origins:** port 5173 and port 6006 are different origins. The parent
  opens a native modal and iframe but does not inspect Phoenix's document.
  Desktop-only embedding avoids clipped mobile controls. The external exact-trace
  link always remains available. Escape works in the parent toolbar; Phoenix
  controls keys while focus is inside its cross-origin frame.
- **React state:** the draft and displayed saved snapshot are separate. Keys reset
  native disclosures/viewers between investigations. History is five completed
  validated snapshots, not backend sessions. No credentials, consent or drafts
  are persisted. Local storage is unencrypted, bounded by count, and has no expiry.
- **Metrics:** agent duration uses a monotonic clock and excludes fixture setup
  and HTTP serialization. Tool attempts include retries. Model requests are
  observed SDK calls including corrections/errors. Each token sum requires that
  metric from every observed call; unavailable is not zero. No dollar cost is
  estimated by APILens. Phoenix's own cost display is not a billing measurement.

## Defend the evaluation claim

Read the [unchanged historical comparison](../../evaluation/reports/v1-v2-experiment-2026-10-21.md).
The dataset contains 18 cases, including 15 bug cases, two no-bug reports and an
insufficient-input control. Each case ran once per version against isolated
fixtures. V2 changed investigation guidance and clarified approved Sandbox writes;
model/settings, limits and dataset were controlled. Strict success requires the
expected status, full grounding, and a correct root cause for bug cases.

V1 recorded **13/18**, V2 **15/18**, retaining all 13 original successes. Root-cause
correctness was 13/15 → 15/15, while fully grounded counts remained 17/18. Mean
agent latency increased 8.614 → 9.629 seconds; total agent tokens fell
118,812 → 113,122. Quality and tool-efficiency labels are separate from success.
Two historical no-bug acceptance failures remained, and one V2 target case was
only partially grounded. A judge's narrower evidence view is a known limitation.

A [separate current-system V3 verification](../../evaluation/reports/v3-verification-2026-10-25.md)
on 2026-10-25 recorded **17/18 strict successes**, retaining all 15 V2 successes
and adding both no-bug cases. The same partially grounded missing-inventory case
remains. Status matches are 18/18, which is not the strict score. V3 is a historical,
descriptive comparison, not another controlled causal experiment. It used more
agent tokens and measured lower latency; do not claim a billing saving. The video
and screenshots in this directory retain their original 2026-10-24 identities.

This supports a measured improvement on these saved runs. It does **not** establish
statistical reliability, generalization, deterministic repeatability, dollar
savings or production readiness. The 24 October browser/media checks are separate
observations, not a full-dataset score. V3 supplies the later full-dataset score;
neither verification rewrites V1/V2 labels.

## Likely interview questions

| Question | Short answer |
| --- | --- |
| Why not let the LLM execute tools? | The model is a decision proposer. Application-controlled validation and limits make permissions testable and failures observable. |
| Why no agent framework? | Four tools and an explicit loop are small enough to trace directly; a framework would not remove the need for these boundaries. |
| Does structured output guarantee correctness? | No. It constrains shape, and semantic validation adds consistency rules. Evidence and evaluation address truth/grounding separately. |
| Is HTTP 500 a tool failure? | Not when the probe successfully received it. That status is precisely the evidence being investigated. |
| Why SQLite and ASGI transport? | Cheap fresh files isolate mutations and correlated logs per demo request; real route/database code still executes without another service. |
| Is this production-safe? | It is a trusted local Sandbox demo, without auth, public deployment, production targets or arbitrary code execution. It is not a production incident agent. |
| What can still fail? | Provider output/availability, grounding, deadlines and trace ingestion; saved trace links can outlive Phoenix retention. See the verification report. |
| Why keep history? | It prevents losing a paid result when drafting the next report. Five browser-local snapshots avoid adding a backend session system; privacy remains a deliberate tradeoff. |
| Why separate evaluators? | Correct root cause, evidence grounding, readability and efficient tool use are different properties; one score can hide a failure. |
| Did V3 fix or replace the historical experiment? | No. V1/V2 remain 13/18 and 15/18. V3 separately measures the current system at 17/18; both no-bug cases pass, but historical output rejection subtypes remain unknown and the difference is not a controlled schema-only effect. |

## Fallback procedure

1. If startup fails, check `docker compose ps`, `/openapi.json` on 8001 and
   `/readyz` on 6006. Preserve volumes; never use a destructive reset as a demo fix.
2. A 503 requires backend credentials and Agent recreation. A 502 is a safe
   provider/execution failure, not an accepted diagnosis. Do not repeatedly click
   retry; each deliberate resubmission may cost money.
3. For delayed traces, reopen the same link later or use its external action.
   Do not rerun an investigation just to obtain a trace.
4. Say **“This is a previously recorded genuine investigation.”** Open Recent
   if the result exists in this browser, or play `apilens-demo.mp4` offline.
   The [capture provenance](README.md) links the video/screenshots to their exact
   recorded result. Do not present a missing Phoenix server as a working live view.
