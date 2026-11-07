# RepAssist — Hotel Booking Conversation Assistant

RepAssist is a **silent** real-time assistant for hotel booking agents. It listens to a conversation between a
Customer and a Hotel Booking Agent, transcribes it live, tracks which checklist items have been covered, suggests
follow-up questions and keeps a running booking report. It never speaks, never makes reservations and never promises
availability — the human agent stays responsible for the customer.

* Backend: Python + FastAPI, WebSockets · Frontend: plain HTML/CSS/JS (AudioWorklet) · Storage: local JSON files
* AI: **Google Gemini only** (official `google-genai` SDK): Live transcription, file transcription, conversation analysis

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then put your key in .env
python main.py              # -> http://localhost:8000
```

`.env`:

```env
GOOGLE_API_KEY=your_key_here      # checked first
# GEMINI_API_KEY=your_key_here    # used if GOOGLE_API_KEY is empty
```

The key is read when the server starts (restart after changing it), is never sent to the browser, never logged, and
`.env` is git-ignored. If no key is present a red banner explains how to add one. The placeholder `your_key_here`
counts as "no key".

`GET /api/health` returns `ai_ready` / `configured`, which **only means a key is present** — not that it is valid or
has quota. `transcription_status` / `analysis_status` stay `not_tested` until the service is used (or until you call
`POST /api/connectivity-check`, which makes one tiny real call to the analysis model).

## Models (all configurable in `.env`)

| Variable | Default | Used for |
|---|---|---|
| `LIVE_TRANSCRIPTION_MODEL` | `gemini-3.5-transcribe-live` | Gemini Live streaming transcription |
| `UPLOAD_TRANSCRIPTION_MODEL` | `gemini-2.5-flash` | Uploaded-recording transcription (`generate_content` with audio) |
| `ANALYSIS_MODEL` | `gemini-2.5-flash` | Checklist analysis, summaries, final reconciliation |

**Verify these for your account.** Model availability differs by account and changes over time. They were chosen from
the model names shipped in the installed SDK; the Live defaults could **not** be exercised end-to-end during
development because that needs a valid key. What *was* verified against the real service: the SDK's Live and
`generate_content` calls reach Google and an invalid key is reported correctly.

The Live config differs by model family (see `LiveTranscriber._config`): a model with `transcribe` in its name gets a
transcription-only config (language codes + diarization); any other Live model is used only for its input
transcription, with TEXT output that is ignored and a "stay silent" instruction. If the Live endpoint rejects the
connection (Google returns HTTP 404 both for an unknown model *and* for a bad key), RepAssist shows a model/key error
instead of silently substituting anything. There is no fallback provider and no simulated transcription.

## How it works

```
Browser mic -> AudioWorklet (16 kHz PCM16, ~100 ms frames) -> WebSocket -> Gemini Live
  -> interim + final transcript events -> UI immediately
  -> finalized segments -> debounced scheduler -> Gemini analysis (new segments + compact state only)
  -> validated merge -> coverage engine (Python) -> topic coverage / questions / live report
```

* **Three independent layers.** Audio streams continuously; transcript events reach the UI as they arrive; analysis
  runs only when new *final* speech exists (`analysis_scheduler.py`): debounce ≈ 2.5 s after the last segment, hard
  ceiling 8 s during continuous speech, ≈ 1 s when a segment contains digits (dates, counts, prices), one analysis in
  flight at a time, nothing during silence. These are targets, not guarantees — Gemini latency varies. Tune with
  `ANALYSIS_DEBOUNCE_S` / `ANALYSIS_MAX_WAIT_S`.
* **Incremental analysis.** Each request carries only the new segments, ~6 segments of earlier context, current criterion
  states, booking info and the running summary — not the whole transcript. Every 10th pass (≤ 300 segments) is a full
  reconciliation, and stopping the session runs a final full-transcript reconciliation.
* **Coverage is arithmetic, not AI.** `coverage = applicable criteria MET / applicable criteria × 100` (full precision,
  rounded only for display). `NOT_APPLICABLE` leaves the denominator, `NEEDS_REVIEW` counts as not met, zero applicable
  criteria shows `N/A`, overall coverage is total-met / total-applicable (never an average of rounded topics). Gemini
  only decides criterion states; it never outputs a percentage.
* **Evidence is verified.** A criterion can only become `MET` / `NOT_APPLICABLE` if the cited segment ids exist and the
  quoted text is supported by them; booking values must cite a real segment. Unsupported updates are rejected and the
  previous state kept. Stale (older-revision) results can't overwrite newer values. Changed values (two rooms → three)
  update the booking info (history kept) while the transcript stays untouched.
* **Invalid AI output** is retried once; then the last valid state is kept and a non-blocking warning shown.
* **Speakers.** Labels come from Gemini's diarization when the model supports it and stay neutral ("Speaker 1") until
  the role is evidenced by the conversation. Click any speaker label to correct it (applies to all segments of that
  speaker; manual corrections always win).
* **Connection recovery.** If Gemini Live drops, RepAssist reconnects (up to 5 times, backoff), buffers about 10 s of audio,
  and tells you how much audio, if any, was lost. Auth/model/quota errors are not retried.
* **Sessions.** `CREATED → CONNECTING → RECORDING → FINALIZING → COMPLETED` (uploads use `PROCESSING`; failures end in
  `FAILED` with a Retry that keeps the transcript). Closing the browser mid-call preserves and finalizes the transcript.
  The UI marks results as *Live provisional analysis* until finalization.

## Features

* **Live Session** — consent, split screen: transcript | topic coverage (expand to see criteria + evidence) | suggested
  questions (3–5, disappear once answered) | live report.
* **Upload Recording** — WAV, MP3, AIFF, AAC, OGG, FLAC (≤ `MAX_UPLOAD_MB`, default 100). Validated by extension, size and
  header bytes. Transcribed by Gemini with timestamps/speakers, then analysed in sections of 20 segments with the dashboard
  updating after each section; step list shows real stages only (no invented percentages). Audio player with
  click-a-segment-to-seek. Files > 18 MB use the Gemini Files API and are deleted from it afterwards. Very long recordings
  are limited by Gemini's output size for a single transcript.
* **Configurations** — create / edit / duplicate / delete checklists (topics, criteria, language, roles). Validated: ≥ 1 topic
  with ≥ 1 criterion, unique ids. A snapshot is stored with each session so later edits never change history. The default
  *Hotel Room Booking Conversation* (8 topics, 33 criteria) is created on first launch only.
* **Recent Sessions** — view, export, delete (removes transcript, report, audio and metadata — the whole session folder).
* **Export HTML** — single self-contained file (summary, booking info, coverage with evidence, questions not asked, next
  action, full transcript, print-friendly for PDF). Uploaded/stored audio is embedded as base64 up to `EMBED_AUDIO_MAX_MB`
  (default 20); above that the report says the audio is **not** embedded. All user content is HTML-escaped.
* **Languages** — English, Tamil, English-Tamil mixed: language codes passed to Live, Tamil/mixed prompts for questions
  and summaries, transcript text kept as spoken. Quality depends on the Gemini model.
* **Diagnostics** — footer "Diagnostics" shows real timings (audio → first transcript, finalized segment → analysis request,
  request duration, UI update, finalization). No conversation content is logged.

## Privacy

* Recording consent is mandatory (live and upload).
* **Audio and transcript text are sent to Google Gemini.** RepAssist is *not* offline and local storage is not local-only
  AI processing. Deleting local files does **not** delete anything already processed or retained by Google under its policies.
* Sessions are stored as JSON under `data/sessions/<id>/`. `SAVE_SESSIONS=false` keeps sessions in memory only (lost on restart,
  uploaded audio removed after processing). `RETENTION_DAYS` > 0 purges older session folders at startup.
* Live audio is **not** stored unless `SAVE_LIVE_AUDIO=true` (then saved as WAV for playback/export).
* No raw transcripts/audio/keys in logs, no analytics/telemetry, nothing in browser storage, WebSocket Origin check.
* Card-like numbers (Luhn-valid 13–19 digits) are redacted from transcripts before storage/analysis, and Gemini is told not to
  extract card data. This is best effort — avoid reading card numbers aloud.
* The server binds to `127.0.0.1` by default and has no login; don't expose it to a network as-is.

## Project layout

```
main.py                     entry point (python main.py)
app/api/                    sessions.py, configurations.py, websocket.py
app/services/               gemini_client, gemini_transcription, gemini_analysis, analysis_scheduler,
                            analysis_merge, coverage_engine, report_service, session_service, audio_service
app/models/  app/utils/     pydantic models; storage + validation
static/ templates/          plain HTML/CSS/JS frontend (static/js/pcm-worklet.js = AudioWorklet)
configuration_templates/    default hotel_booking.json
data/                       sessions and configurations (git-ignored)
tests/                      pytest suite
```

## Tests

```bash
python -m pytest -q
```

Covers the coverage maths (all spec examples, N/A, needs-review, invalidation, duplicates, rounding), evidence validation,
stale/out-of-order merges, the scheduler (debounce, max wait, silence, single flight), transcript interim/final/dedupe,
session lifecycle (finalize, failure + retry, no-speech, restart), the seven hotel scenarios, upload flow, export escaping,
deletion and the HTTP/WebSocket API.

**Important:** the scenario tests drive RepAssist's own logic with a *scripted stand-in* for Gemini's analysis output. They prove
the scoring, validation, merging and UI plumbing — not Gemini's transcription or judgement quality. Test with a real key and
real recordings before relying on it.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Red banner "No Gemini API key" | Put the key in `.env`, restart `python main.py` |
| "authentication error" | Key invalid/revoked; check it, restart |
| "Gemini Live rejected the connection (HTTP 404)" | Invalid key **or** a Live model your account can't use — run `POST /api/connectivity-check`, then set `LIVE_TRANSCRIPTION_MODEL` to a Live model available to you |
| "quota exceeded" | Rate limit/quota; wait or raise limits |
| Microphone denied/not found | Allow mic in browser (use `http://localhost`, which counts as secure) |
| Speakers all "Speaker 1/Unknown" | Model/diarization unavailable, or one mic hears one side poorly — correct labels by clicking them |
| Upload transcript unreadable | Gemini returned bad JSON twice; use **Retry** |
| Analysis warning "invalid response" | Non-blocking; the next pass retries the same segments |
