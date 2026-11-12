# PROMPT — Build "Sales Assist" (Hotel Booking Conversation Assistant)

You are a senior full-stack Python engineer. Build the complete application described below, end to end: all source files, configuration, requirements, README and tests. Read the whole brief first. Part 1 is the business context and Part 2 the product behaviour. Part 3 is the technical specification. Do not skip Part 3 — it contains hard-won details that decide whether the app works.

---

## PART 1 — BUSINESS CONTEXT

**Product name:** Sales Assist — Hotel Booking Conversation Assistant.

**Problem.** Hotel reservation agents take booking calls and desk conversations. They must collect a fixed set of information (dates, guests, room type, price acknowledgement, policies, contact details, confirmation). Agents forget questions, customers hang up with gaps, managers cannot audit quality, and notes are typed after the call.

**Solution.** A *silent* assistant that listens to the conversation (live microphone or an uploaded recording), transcribes it, and continuously checks it against a configurable checklist. It shows the agent, in near real time, what has been covered, what is still missing, and which question to ask next. At the end it produces a structured booking report.

**Users.**
- *Reservation agent* — watches the live screen during a call; wants "what do I still need to ask?".
- *Team lead / QA manager* — reviews finished calls, coverage scores, and the exported report.
- *Admin* — edits the checklist (topics and criteria) without code changes.

**Business principles (non-negotiable).**
1. **Silent and advisory.** It never speaks, never makes reservations, never promises anything to a customer. It only observes and suggests.
2. **No hallucination.** Every "met" criterion must be backed by a quote from the transcript. Never invent prices, rates, taxes, availability, policies, names or booking IDs. "Interest in a room" is not a confirmed booking.
3. **Honest status.** Show provisional results while live and final results only after the closing reconciliation. Show errors and warnings plainly; never pretend a service worked.
4. **Privacy.** Recording consent must be ticked before any audio is processed. Redact payment-card numbers from transcripts. Audio and text go to Google Gemini — say so in the README. Keep the API key out of logs and out of the UI.
5. **Language.** English, Tamil, and mixed English-Tamil conversations. Keep each word in the language spoken; never translate transcripts.
6. **Fast feedback.** Target: a spoken fact appears in the coverage panel within roughly 4–7 seconds.

**Success measures.** Coverage % per call, time-to-first-transcript, time-to-coverage-update, fewer missed questions, exported reports used for QA.

---

## PART 2 — PRODUCT BEHAVIOUR

### 2.1 Screens (Streamlit app, light/white theme, wide layout)
Top tab bar styled as pills (white bar, the selected tab has a light-blue background and blue bold text, no underline):

1. **Live Session**
   - Inputs: Configuration (select), Session name, Language (English / Tamil / English-Tamil (Mixed)), consent checkbox ("I confirm the participants consented to this recording.").
   - Browser microphone via a WebRTC component; START begins a live session, STOP finalizes it.
   - While speaking, the report below updates in place about once per second (no page change).
2. **Upload Recording**
   - Same inputs plus a file uploader (WAV, MP3, AIFF/AIF, AAC, OGG, FLAC; max 100 MB; validate extension, size, and file signature).
   - "Start analysis": shows the audio player and fills the report live as transcription and analysis progress.
3. **Configurations** — fully editable checklist editor (see 2.4).
4. **Recent Sessions** — list of saved sessions (name, configuration, status, overall coverage) with Open and Delete; opening shows the saved report.

### 2.2 The report (same component for live, upload and saved sessions)
- **Header card:** session name, configuration name, elapsed time (mm:ss), status pill (Recording / Processing / Completed / Failed), and a "Final results" or "Live provisional analysis" pill.
- **Overall coverage card:** "Overall Coverage: 27.27%  9 of 33 applicable criteria met" with a progress bar.
- **Audio player** (when audio exists; rendered once so playback is not restarted by refreshes).
- **Two columns:**
  - Left — **LIVE TRANSCRIPT** (scrollable): `[mm:ss] Speaker:` then text; alternating row shading; the in-progress (interim) text shown greyed/italic at the end.
  - Right — **TOPIC COVERAGE**: per topic a name, percentage, progress bar, "x of y criteria completed", a status tag (Not Started / In Progress / Needs Review / Complete / Not Applicable) and the criteria list with ✓ (met), ! (needs review) or ○ (not met). Criteria are shown once a topic has progressed.
- **Timing caption:** speech→first transcript ms, last analysis wait ms, Gemini request ms, number of analysis runs.
- **Three expanders:** Summary & next action (plus booking status); Booking details (grouped sections, ✅ when confirmed); Suggested questions & outstanding items.
- **Download HTML report** button (self-contained printable HTML; embed audio if ≤ 20 MB).
- Show session warnings (yellow) and error (red) above the panels.

### 2.3 Business rules
- **Coverage = applicable criteria MET ÷ applicable criteria × 100.** NOT_APPLICABLE criteria leave the denominator; NEEDS_REVIEW counts as not met. Compute overall from raw counts (never average rounded topic values). A criterion id is counted once. Display with up to 2 decimals, trailing zeros trimmed ("27.27%", "60%"). No applicable criteria → "N/A". The AI never outputs percentages; only code computes them.
- **Topic status:** Not Applicable if none applicable; Complete if all met; Needs Review if any needs review; In Progress if some met; else Not Started.
- **Criterion statuses:** `MET`, `NOT_MET`, `NOT_APPLICABLE`, `NEEDS_REVIEW`.
  - MET and NOT_APPLICABLE require evidence: cited segment ids must exist and the quote must be supported by those segments' text. Otherwise reject the update and keep the previous state.
  - NOT_APPLICABLE only when the conversation shows why (e.g. the customer says there are no children), never merely because something was not discussed.
  - Tentative values are not confirmed. If the customer changes a value, use the latest; mark NEEDS_REVIEW only for unresolved contradictions.
  - Reject stale results (older revision than what is already applied for that criterion/field).
- **Booking record** (fields, all optional, each citing a source segment): customer_name, check_in_date, check_out_date, nights, adults, children, rooms, room_type, bed_preference, special_requests, availability_discussed, room_rate, additional_charges, total_estimated_cost, contact_number, email. Keep previous values when changed. Report sections: Booking Overview, Room Requirements, Pricing and Availability, Contact; unknown values display "Not discussed"; unknown extra fields go to "Other Details".
- **Booking status:** Inquiry, Options Discussed, Pending Confirmation, Confirmed, Declined, Follow-up Required (or Unknown).
- **Suggested questions:** up to 5, polite and short, for criteria not yet MET/NOT_APPLICABLE, most important first (priority high → low); they disappear once the criterion is satisfied. Never assume unavailable inventory or invented prices.
- **Speakers:** live transcription does **not** provide speaker labels, so speakers start as "Unknown"/neutral. The analysis may add speaker hints (speaker → role) only when the content makes the role clear; never assume the first speaker is the agent. Support manual speaker→role correction in the data layer.
- **Final reconciliation** runs when a session ends: re-evaluate every criterion from scratch against the full transcript, write the final summary, outcome ("never claim a booking that was not finalized"), missing information and questions not asked. If it fails, keep the transcript and mark the session Failed with a retry path.
- **Retention:** sessions persist as JSON on disk, optional retention purge in days (0 = keep). Live audio is not stored unless `SAVE_LIVE_AUDIO=true`.

### 2.4 Configuration editor
Configuration = name, description, roles (comma separated), language, ordered topics, each with ordered criteria (label, instruction, optional suggested question, priority high/medium/low).
UI per the layout: Name, Description, Roles, Language; then one bordered box per topic with "Topic N name" and one row per criterion (label field, instruction field, **Remove** button), **Add criterion**, **Delete topic**; below: **Add topic**, **Save configuration**, **Delete configuration** (hidden when only one exists), and a selector including "➕ New configuration".
Validation: name required; at least one topic; each topic needs ≥ 1 criterion; ids are slugified and unique (criterion ids globally unique); keep existing ids when labels are edited so old sessions stay valid; drop blank rows on save; show validation messages. Each session stores a **snapshot** of its configuration.

### 2.5 Default configuration — "Hotel Room Booking Conversation" (seed on first run)
Roles: Hotel Booking Agent, Customer. Language: English. Description: "Tracks essential customer requirements, room availability discussions, pricing, policies, and booking confirmation."

| Topic | Criteria (each has an instruction, a default follow-up question and a priority) |
|---|---|
| Booking Dates | Check-in Date; Check-out Date; Number of Nights; Date Confirmation |
| Guest Information | Number of Rooms; Primary Guest Name |
| Room Preferences | Room Type |
| Pricing and Charges | Room Rate; Total Estimated Cost; Customer Acknowledgement |
| Hotel Policies | Check-in and Check-out Times; Cancellation Policy |
| Contact and Reservation Details | Customer Name; Contact Number |
| Booking Confirmation | Booking Details Recap; Customer Confirmation; Reservation Status; Next Steps |

= 7 topics, 18 criteria. Example instruction: Check-in Date → "Customer's intended arrival date is clearly established. A tentative date is not confirmed." Customer Confirmation → "Customer confirms or clearly declines the proposed booking. Interest alone is not confirmation."

---

## PART 3 — TECHNICAL SPECIFICATION

### 3.1 Stack
- Python 3.12 (avoid 3.14: `av` wheels may be missing), **Streamlit** (≥ 1.40) single entry file `main.py`, run with `streamlit run main.py`. No FastAPI/uvicorn.
- **Google Gemini only** via the `google-genai` SDK. Pydantic v2, python-dotenv.
- Live microphone via **streamlit-webrtc** (+ `av`).
- `requirements.txt`: `streamlit>=1.40`, `streamlit-webrtc>=0.60`, `av>=12`, `google-genai>=1.0`, `python-dotenv>=1.0`, `pydantic>=2.7`.
- `.streamlit/config.toml`: `[theme] base="light"`, primaryColor `#2563eb`, background `#f3f4f6`, secondary background `#ffffff`, text `#111827`.

### 3.2 Project layout
```
main.py                      Streamlit UI (tabs, report renderer, live loop)
app/config.py                Settings from env/.env (+ Streamlit secrets copied to env)
app/models/{session,analysis,configuration}.py   Pydantic models
app/services/
  gemini_client.py           shared client + error classification
  gemini_transcription.py    live transcriber, transcript assembler, file transcription
  gemini_analysis.py         prompt builder + analysis call + JSON validation
  analysis_scheduler.py      debounced, single-flight analysis scheduling
  analysis_merge.py          evidence validation + merging results into session state
  coverage_engine.py         pure-Python coverage maths
  session_service.py         SessionRuntime (live/upload pipelines) + SessionManager
  report_service.py          dashboard, final report, HTML export
  audio_service.py           PCM16 16 kHz → WAV
app/utils/{storage,validation}.py   atomic JSON stores, audio validation, id safety, card redaction, mm:ss
configuration_templates/hotel_booking.json   default checklist
data/{sessions,configurations}/ (git-ignored, with .gitkeep)
```

### 3.3 Settings (environment, `.env` locally, Streamlit **Secrets** in the cloud)
`GOOGLE_API_KEY` (or `GEMINI_API_KEY`; ignore placeholder values like `your_key_here`), `LIVE_TRANSCRIPTION_MODEL` (default `gemini-3.5-transcribe-live`), `UPLOAD_TRANSCRIPTION_MODEL` (`gemini-2.5-flash`), `ANALYSIS_MODEL` (**`gemini-3.1-flash-lite`**), `ANALYSIS_DEBOUNCE_S` (1.0), `ANALYSIS_MAX_WAIT_S` (4.0), `ANALYSIS_THINKING_BUDGET` (0), `SAVE_SESSIONS` (true), `SAVE_LIVE_AUDIO` (false), `RETENTION_DAYS` (0), `MAX_UPLOAD_MB` (100), `EMBED_AUDIO_MAX_MB` (20), `REPASSIST_DATA_DIR`. At the top of `main.py`, copy every top-level string in `st.secrets` into `os.environ` (if unset) **before** `Settings()` is created. Never log or display the key; scrub it from error messages.

### 3.4 Data models (Pydantic)
- `Segment`: id (`seg_001`…), speaker_key, speaker (display), text (card-redacted), start, end.
- `Session`: id, name, language, mode (`live|upload`), status (`CREATED, CONNECTING, RECORDING, PROCESSING, FINALIZING, COMPLETED, FAILED`), created_at, duration_s, config (snapshot dict), consent, segments, `criteria: dict[id → CriterionState{status, evidence{text, speaker, segment_id(s), timestamp}, reason, revision}]`, `booking: dict[field → BookingField{value, confirmed, source_segment_id, revision, previous[]}]`, booking_status, summary, next_action, question_pool, evaluated_through, revision, applied_revision, analysis_count, finalized, final_report, speaker_map, warnings, error, audio_file/mime, timings, overall_display.
- `AnalysisResult` (what the LLM returns, validated strictly; status strings upper-cased; accept `booking_updates` as list or object form): `evaluated_through_segment_id`, `criteria_updates[{topic_id, criterion_id, status, evidence_segment_ids, evidence_quote, reason}]`, `booking_updates[{field, value, confirmed, source_segment_id}]`, `suggested_questions[{criterion_id, question, priority}]`, `speaker_hints[{speaker_key, role, evidence_segment_ids}]`, `report_update{summary, recommended_next_action, booking_status}`, optional `final_report{outcome, missing_information[], questions_not_asked[]}`.
- Persist each session as `data/sessions/<id>/session.json` using **atomic writes** (write temp file, then rename). Config store: one JSON per configuration in `data/configurations/`, seeded from `configuration_templates/` on first run only. IDs must match `^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$` (prevent path traversal).

### 3.5 Gemini usage — details that matter
- Create the client with **`http_options=HttpOptions(api_version="v1beta")`**. The SDK default (v1) has no Live endpoint and returns HTTP 404.
- **Live transcription** (`client.aio.live.connect(model=LIVE_MODEL, config=...)`):
  - Model name contains "transcribe": `LiveConnectConfig(input_audio_transcription=AudioTranscriptionConfig(language_codes=[...]))` only. **Do not enable diarization** — the Live API rejects it. Language codes: English `en-US`, Tamil `ta-IN`, mixed `["en-US","ta-IN"]`.
  - Other (conversational) Live models: response modality TEXT, `input_audio_transcription` enabled, system instruction "You are a silent transcription device. Never respond to the speakers."
  - Send audio as raw **PCM16, mono, 16 kHz**, MIME `audio/pcm;rate=16000`, via `send_realtime_input(audio=Blob(...))`; send `audio_stream_end=True` on stop.
  - Receive loop handles `interim_input_transcription`, `input_transcription` (text + `finished`) and `turn_complete`; on `go_away` raise to trigger reconnect.
  - Reconnect up to 5 times with exponential backoff (cap 15 s); buffer audio frames in a bounded deque while reconnecting and report any lost audio honestly. Classify errors (see below).
- **TranscriptAssembler** (pure logic, unit-tested with a fake clock) turns interim/incremental events into final segments:
  - Handles both *cumulative* text (new text starts with the buffer) and *delta* text (append). Join deltas safely so words do not run together ("30Could").
  - Finalize on: `finished`, `turn_complete`, **1.5 s of silence**, or a **forced split** when one unbroken utterance exceeds **~7 s or ~30 words** (lower these for faster feedback). After a forced split remember the already-emitted words as a prefix and strip it from subsequent cumulative text so nothing is duplicated; clear the prefix on a natural finalization. Drop duplicate finals. Emit `("interim", …)` and `("final", …)` events. Tick every 0.4 s.
- **Upload transcription:** one `generate_content` call with the audio (inline bytes up to ~18 MB, otherwise the Files API), JSON output `{speech_detected, segments[{start "MM:SS", speaker "Speaker N", role, text}]}`, temperature 0, large max output tokens. Prompt: verbatim, keep Tamil in Tamil script and English in English, one segment per speaker turn, role only if clear, never invent speech. Merge consecutive same-speaker segments; end time = next start.
- **Analysis call** (`generate_content`, `response_mime_type="application/json"`, temperature 0.1):
  - System prompt = business rules from 2.3 + a language rule per language + the exact JSON shape. Include: judge only from the transcript; evidence ids and a quote are mandatory for MET/NOT_APPLICABLE; only return updates for criteria that *change* because of the NEW segments; never output percentages; never extract card numbers or security codes; suggested questions ≤ 5; `report_update.summary` is a concise running summary (~80 words).
  - User payload (compact JSON): session id, language, roles, checklist (topic_id, criterion_id, label, instruction), current non-NOT_MET criterion states with 120-char quotes, current booking info, current summary, up to 6 earlier context segments, and the NEW segments (id, speaker, speaker_key, mm:ss, text).
  - Thinking: for `gemini-2.5-flash` set `ThinkingConfig(thinking_budget=0)` (measured: ~11 s → ~4 s); do **not** send `thinking_budget` to 3.x models (some return HTTP 400). Default model `gemini-3.1-flash-lite` measured ≈ 2.8 s on a sample call.
  - Retry once on malformed JSON, then raise `invalid_response` and keep previous state. Single analysis in flight (async lock).
  - Final pass adds an addendum: full transcript, re-evaluate everything including invalidations, produce `final_report`.
- **Model availability trap:** a model can appear in `models.list()` yet return HTTP 404 on `generate_content` (e.g. `gemini-2.5-flash-lite` was listed but not callable). Verify any default model with a real call before shipping it, and make the models configurable.
- **Error classification** → kinds `missing_key | auth | quota | model | network | invalid_response | other` with plain-English messages. HTTP 401/403 → auth; 400/404 → model-or-key; 429 → quota; 5xx → network; Live handshake errors expose the status on `exc.response.status_code`. The 404 message must **not** blame "Live" for REST calls: say the model name may be unavailable for the key.
- Provide a connectivity check (tiny `generate_content`) and keep honest service status (`not_tested | ok | error:<kind>`).

### 3.6 Analysis scheduling (live)
Event-driven, not polling. `AnalysisScheduler.notify(text)` is called for every *final* segment; it analyses when new content exists, after a debounce (**1.0 s**, **0.3–1 s if the segment contains digits**, since dates/counts/prices are urgent), never waiting more than **max-wait 4 s** while speech continues; ignore fragments under 3 words unless urgent or max-wait reached; exactly one analysis in flight, events arriving meanwhile accumulate; silence triggers nothing. Every 10th analysis (while ≤ 300 segments) does a full-transcript reconciliation. On stop: flush the transcriber, drain the scheduler, run the final reconciliation. Track timings per analysis (`segments_to_request_ms`, `request_ms`, `ui_update_ms`) and `first_transcript_ms`.
Upload pipeline: transcribe the file → add segments → analyse in chunks of 20 segments (progressive, dashboard updates after each) → final reconciliation.

### 3.7 Merging results safely (`analysis_merge`)
Never trust the model: validate criterion ids against the snapshot config, require existing segment ids and a quote supported by those segments' text for MET/NOT_APPLICABLE (normalize case/punctuation; allow partial match), reject stale revisions, require booking updates to cite an existing segment, keep `previous` values, apply speaker hints without overriding manual mappings, update summary/next action/booking status (only allowed status values), rebuild the suggested-question pool, and return the list of rejected updates for diagnostics.

### 3.8 Streamlit architecture (important)
- `get_mgr()` with `@st.cache_resource` builds Settings, stores, GeminiClient and SessionManager once.
- **One persistent asyncio event loop in a daemon thread** (`@st.cache_resource`) runs live work, because Streamlit reruns the script constantly. Schedule with `asyncio.run_coroutine_threadsafe(rt.start_live(), loop)` and `...stop_live()` (`.result(180)` with a spinner "Finalizing report…").
- Live audio: `webrtc_streamer(key=…, mode=SENDONLY, audio_frame_callback=on_audio, media_stream_constraints={"audio": True, "video": False}, rtc_configuration=<Google STUN server>)`. The callback runs in another thread: resample each frame with `av.AudioResampler(format="s16", layout="mono", rate=16000)` (handle both list and single-frame return), then `loop.call_soon_threadsafe(rt.push_audio, pcm_bytes)`. Keep the active runtime and resampler in a shared holder dict (`@st.cache_resource`), not in the closure.
- Flow: when `ctx.state.playing` becomes true and no runtime is active → require consent, create a `live` session, store the runtime in the holder, start live. While playing: `while ctx.state.playing:` redraw the report into an `st.empty()` container every 1 s using `rt.session`, `rt.interim` and elapsed time, then `st.rerun()` after the loop. When it stops → finalize, clear the holder, then show the saved report.
- Upload tab: run `asyncio.run(run_live(...))`: `create_task(rt.process_upload())` and redraw every second until done; afterwards drop the runtime from the manager cache so the next view reloads from disk (avoids event-loop-bound locks).
- Import `streamlit_webrtc` inside `try/except ImportError` and show a clear message in the Live tab if it is missing (the rest of the app must still run).
- Render the report with `st.markdown(..., unsafe_allow_html=True)` using small CSS classes; **HTML-escape every dynamic string**. Style the tab bar with CSS on `.stTabs [data-baseweb="tab-list"]`, `[data-baseweb="tab"]`, `[aria-selected="true"]`, and hide `tab-highlight`/`tab-border`.
- Use widget keys that include stable per-item ids (e.g. `uuid` per topic/criterion) in the configuration editor so edits survive add/remove reruns; mutate the draft, then `st.rerun()`. Set the selectbox value through a "next selection" session-state key applied *before* the widget is created.

### 3.9 Deployment notes (Streamlit Community Cloud)
- Main file `main.py`; **Advanced settings → Python 3.12**; Secrets in TOML: `GOOGLE_API_KEY = "…"` (any other setting as a top-level string).
- No `.env` in the repo (git-ignored). The container disk is ephemeral: `data/` resets on redeploy, so the default checklist comes from `configuration_templates/`.
- WebRTC on Streamlit Cloud may need a TURN server on restrictive networks; STUN alone often works. Browser mic permission and HTTPS are required; test in Chrome (Firefox reports `NotFoundError` when it cannot open a device).
- Health check is Streamlit's own; do not start another server (uvicorn) inside the script.

### 3.10 Quality bar and tests
Write pytest tests for: coverage maths (denominators, NOT_APPLICABLE, rounding, no double counting), TranscriptAssembler (cumulative and delta streams, duplicates, speaker change, forced split with prefix stripping, rejoined text equals the original), scheduler (debounce, urgent, max-wait, single flight, silence), merge rules (evidence required, stale rejection, quote support), validation (ids, audio type/size/signature, card redaction), configuration validation, and report building. Use a scripted fake Gemini analysis so tests need no network.

**Acceptance criteria**
1. `streamlit run main.py` opens with four pill tabs, white theme, no errors with no API key (shows a clear "key missing" message).
2. Uploading a WAV fills transcript, coverage and booking details progressively on the same page, then shows Final results and a downloadable HTML report.
3. Live mode: after START and speaking, transcript lines and coverage appear in about 4–7 s per fact; STOP produces final results.
4. Editing the configuration (add/remove criteria and topics, save) changes the coverage denominator for new sessions without affecting old ones.
5. No criterion is ever marked MET without a supported quote; errors from Gemini are displayed in plain English and never contain the API key.
