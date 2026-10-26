# Demo screenshots and recording — 24 October 2026

[Watch the 71-second MP4](apilens-demo.mp4) ·
[Demo/interview guide](WALKTHROUGH.md) · [Verification report](VERIFICATION.md)

These are actual captures of APILens on `final-portfolio-readiness`, based on
PR #33 plus the status-schema guidance fix. They are separate from the historical
V1/V2 assets in the parent directory and do not replace historical measurements.

The later [V3 full-dataset verification](../../evaluation/reports/v3-verification-2026-10-25.md)
on 25 October recorded 17/18 strict successes, including both no-bug cases. It is
a separate evaluation, not the source of these screenshots or this video; see
the [V3 protocol](../../evaluation/V3.md) for its scope and provenance.

## Screenshots

| File | What it shows |
| --- | --- |
| [Main screen](frontend-main.png) | Real initial frontend, no result or automatic request. |
| [Scenario selection](scenario-workflow.png) | Missing-inventory selected; four existing symptom categories. |
| [Live waiting state](frontend-loading.png) | Explicitly submitted live report; no invented tool progress. |
| [Outcome and metrics](investigation-outcome.png) | Real `RESOLVED` diagnosis, measured duration and usage. |
| [Evidence](investigation-evidence.png) | HTTP 500, same-request logs and implementation support. |
| [Chronological timeline](investigation-timeline.png) | Actual tool order; successful HTTP probe observing HTTP 500. |
| [Phoenix root](phoenix-trace.png) | The corresponding live `DebugSession` in the original embedded Phoenix UI. |
| [Phoenix HTTP span](phoenix-http-span.png) | Actual tool-success and HTTP-500 metadata. |
| [Mobile outcome](mobile-result.png) | Same genuine result reopened from local history, 390 × 844. |
| [Mobile technical view](mobile-timeline.png) | Expanded same-result technical information. |
| [Mobile Phoenix fallback](mobile-phoenix-link.png) | Exact external trace action, without the desktop iframe. |

Desktop screenshots are unaltered 1440 × 900 browser viewport captures. Mobile
screenshots are unaltered 390 × 844 captures. The mobile saved-result label is
intentional: reopening made no new model request. Images were individually
inspected for legibility, clipping, truthful content and sensitive information.

## Video provenance

`apilens-demo.mp4` is **71 seconds**, silent, H.264/yuv420p in a fast-start MP4,
1440 × 1000, about 4.6 MiB. The source is continuous browser footage of live
session 9: selecting the sample, acknowledging cost, submitting once, waiting,
reading the outcome/evidence/timeline, opening its Phoenix trace, and copying
the diagnosis. No mocked response or scripted provider was used in that footage.

Only the first 71 seconds were selected from the longer QA recording. There are
no internal cuts, time compression, repeated scenes or synthetic UI frames. A
100-pixel caption band was added above the original 1440 × 900 capture. Captions
explain what actually happened; no diagnosis or result pixels were changed. There
is no narration, music, unrelated desktop content or credential material.

| Recorded identity | Value |
| --- | --- |
| Demo scenario | `missing-inventory` |
| Model / reasoning | `gpt-6-sol` / `medium` |
| Trace | `4631295a53f64f928590a55fac426154` |
| HTTP/log request | `159f8b18-aa7e-44c5-a99c-24f4db588be1` |
| Session | `934b0a39-9c3d-4d60-ba08-5a620d67b710` |
| Root span | `1c2c465367b00179` |
| Selected HTTP span | `1752094030ced3c1` |
| Agent outcome | `RESOLVED` |
| Agent-only duration | 9.986 seconds |
| Tool attempts / model requests | 4 / 5 |
| Input / output / total tokens | 8,487 / 474 / 8,961 |

The HTTP span and UI metadata agree: tool success is true while the observed
Sandbox response is HTTP 500. Phoenix's `Unset` is an OpenTelemetry span status,
not the final diagnosis. Its dollar display is an estimate, not a verified bill.
The same trace loaded at both 1440 × 900 and 1280 × 720; ingestion was actually
observed, not inferred from the ID or iframe load event.

The final MP4 was fully decoded without errors, played in Chromium, sought to
representative report/loading/result/evidence/timeline/Phoenix/closing frames,
and played through its end. Encoded frames and the final caption framing were
visually inspected. [SHA256SUMS](SHA256SUMS) identifies the final media files.

## Privacy and fallback

Only the public sample report, structured diagnosis, sanitized timeline, measured
usage and safe trace metadata are shown. No raw provider messages, hidden reasoning,
credentials, private logs, arbitrary source dump or unrelated personal information
is included. Temporary browser recordings and private QA files are outside Git.

Live trace links depend on this machine's Phoenix storage and may later expire.
The screenshots and MP4 remain usable offline. When presenting them, say
**“This is a previously recorded genuine investigation.”** Opening a saved browser
result also makes no fresh request. Future model runs can choose different tools,
produce a different diagnosis, or fail safely.

The [12-session ledger](live-verification.json) preserves safe current-verification
metadata. It is not an evaluation score and contains no rejected output. Offline
simulated error/limit checks were used only for UI validation, not these media.
