"""Sales Assist - Hotel Booking Conversation Assistant (Streamlit). Run with:  streamlit run main.py"""
import asyncio
import copy
import html
import os
import re
import threading
import time
import uuid

import streamlit as st

# Streamlit Cloud secrets -> environment, before Settings() reads it (optional overrides like ANALYSIS_MODEL work too).
try:
    for _k in list(st.secrets):
        if isinstance(st.secrets[_k], str) and not os.getenv(_k):
            os.environ[_k] = st.secrets[_k]
except Exception:  # noqa: BLE001  (no secrets file locally)
    pass

try:
    from streamlit_webrtc import WebRtcMode, webrtc_streamer
except ImportError:  # live mic unavailable; upload still works
    webrtc_streamer = None

from app.config import Settings
from pydantic import ValidationError

from app.models.configuration import LANGUAGES, Configuration
from app.services import report_service
from app.services.gemini_client import GeminiClient
from app.services.session_service import SessionManager
from app.utils.storage import ConfigStore, SessionStore
from app.utils.validation import fmt_ts, validate_audio

st.set_page_config(page_title="Sales Assist", page_icon="🏨", layout="wide")

CSS = """<style>
.stApp{background:#f3f4f6}.block-container{padding-top:1.5rem;max-width:1400px}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:16px 20px;margin-bottom:14px;color:#111827}
.hdr{display:flex;gap:16px;align-items:center;flex-wrap:wrap}.hdr b{font-size:20px}.hdr .cfg{color:#4b5563;font-size:18px}.hdr .time{font-size:20px;font-weight:700}
.pill{padding:4px 14px;border-radius:999px;font-size:14px;font-weight:600}.green{background:#e7f6ec;color:#15803d}.red{background:#fde8e8;color:#b91c1c}.amber{background:#fef3c7;color:#b45309}.gray{background:#f3f4f6;color:#4b5563}
.bar{background:#e5e7eb;border-radius:999px;height:8px;overflow:hidden;margin-top:8px}.bar i{display:block;height:100%;background:#2563eb}
.sec{font-size:13px;font-weight:700;letter-spacing:.08em;color:#4b5563;margin-bottom:12px}
.seg{padding:8px 10px;border-radius:6px;margin-bottom:4px;font-size:15px}.seg.alt{background:#f9fafb}.seg .ts{color:#6b7280;font-size:13px;margin-right:6px}.seg .sp{color:#1d4ed8;font-weight:700}
.tr{height:560px;overflow-y:auto}
.topic{border-bottom:1px solid #e5e7eb;padding:10px 0}.topic .row{display:flex;justify-content:space-between;font-size:17px;font-weight:700}
.topic .sub{display:flex;justify-content:space-between;color:#6b7280;font-size:14px;margin-top:6px}.topic .tag{background:#f3f4f6;border-radius:999px;padding:2px 10px;font-size:13px}
.crit{display:flex;gap:12px;align-items:center;font-size:16px;padding:5px 4px}.crit .ok{color:#15803d;font-weight:700;width:16px}.crit .no{color:#9ca3af;width:16px}.crit .rv{color:#b45309;font-weight:700;width:16px}
.stTabs [data-baseweb="tab-list"]{background:#fff;gap:6px;padding:8px 14px;border-radius:10px;border:1px solid #e5e7eb}
.stTabs [data-baseweb="tab"]{height:auto;padding:10px 18px;border-radius:8px;background:transparent}
.stTabs [data-baseweb="tab"] p{font-size:18px;font-weight:500;color:#1f2937}
.stTabs [aria-selected="true"]{background:#eff6ff}.stTabs [aria-selected="true"] p{color:#1d4ed8;font-weight:700}
.stTabs [data-baseweb="tab-highlight"],.stTabs [data-baseweb="tab-border"]{display:none}
.ask{background:#eff6ff;border:1px solid #bfdbfe;border-left:4px solid #2563eb}.ask .sec{color:#1d4ed8;margin-bottom:6px}.ask .q{font-size:16px;padding:3px 0;color:#111827}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.chip{font-size:13px;padding:3px 10px;border-radius:999px;border:1px solid #e5e7eb;background:#f9fafb;color:#6b7280}
.chip.m{background:#e7f6ec;border-color:#bbf7d0;color:#15803d}.chip.r{background:#fef3c7;border-color:#fde68a;color:#b45309}
.mobile-sub{display:inline}
.snap{flex-basis:100%;color:#374151;font-size:15px;order:5}.snap .u{color:#b45309}
.nba{background:#eff6ff;border:1px solid #bfdbfe;border-left:4px solid #2563eb}.nba .top{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:6px}
.nba .top .sec{margin:0;color:#1d4ed8}.nba .q{font-size:16px;padding:4px 0;color:#111827}.nba .q.review{color:#92400e}
.nba .tg{display:inline-block;font-size:12px;font-weight:700;border-radius:999px;padding:1px 9px;margin-right:6px;background:#dbeafe;color:#1d4ed8}.nba .q.review .tg{background:#fde68a;color:#92400e}
.crit{flex-wrap:wrap;gap:4px 10px}.crit .cl{font-weight:600}.crit .cv{color:#1d4ed8}.crit .cf{font-size:12px;font-weight:700;color:#b45309;background:#fef3c7;border-radius:999px;padding:1px 8px}
.crit .cp{color:#9ca3af;font-size:13px}.line{font-size:14px;color:#374151;margin-top:6px}.line b{color:#15803d}
@media (max-width:640px){
  .block-container{padding:3.2rem .7rem 120px .7rem!important}
  h2{font-size:1.5rem!important}.mobile-sub{display:none}
  .card{padding:11px 13px;margin-bottom:10px;border-radius:12px}
  .hdr{gap:8px}.hdr b{font-size:17px}.hdr .cfg{font-size:14px;flex-basis:100%;order:3}.hdr .time{font-size:17px}.pill{padding:3px 10px;font-size:12.5px}
  .tr{height:340px}.seg{font-size:14.5px}.topic{padding:8px 0}.topic .row{font-size:16px}
  .stTabs [data-baseweb="tab-list"]{padding:5px 6px;gap:2px;overflow-x:auto}
  .stTabs [data-baseweb="tab"]{padding:8px 10px}.stTabs [data-baseweb="tab"] p{font-size:14.5px}
  /* floating START / STOP bar: always reachable while scrolling the report */
  .st-key-live_ctrl{position:fixed!important;left:8px!important;right:84px!important;width:auto!important;max-width:none!important;bottom:10px;z-index:1000;background:#f3f4f6;border:1px solid #cbd5e1;box-sizing:border-box;overflow:hidden;
    border-radius:14px;padding:2px 10px;box-shadow:0 6px 20px rgba(15,23,42,.22)}
}
</style>"""


@st.cache_resource
def get_mgr() -> SessionManager:
    s = Settings()
    configs = ConfigStore(s.configs_dir, s.templates_dir)
    configs.seed_defaults()
    return SessionManager(s, GeminiClient(s), SessionStore(s.sessions_dir, persist=s.save_sessions), configs)


@st.cache_resource
def bg_loop() -> asyncio.AbstractEventLoop:
    """One persistent event loop in a background thread: runs Gemini Live + analysis while Streamlit reruns."""
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    return loop


@st.cache_resource
def live_holder() -> dict:
    """State shared with the WebRTC audio thread: the active live runtime and the 16 kHz resampler."""
    return {"rt": None, "rs": None}


def on_audio(frame):
    """Runs in the WebRTC thread for every mic frame: resample to 16 kHz mono PCM16 and push to Gemini Live."""
    import av

    h = live_holder()
    rt = h["rt"]
    if rt is not None:
        if h["rs"] is None:
            h["rs"] = av.AudioResampler(format="s16", layout="mono", rate=16000)
        out = h["rs"].resample(frame)
        for f in (out if isinstance(out, list) else [out]):
            if f is not None:
                bg_loop().call_soon_threadsafe(rt.push_audio, f.to_ndarray().tobytes())
    return frame


RTC = {"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]}


def e(x) -> str:
    return html.escape(str(x))


def is_mobile() -> bool:
    """Phone layout: ?layout=mobile|desktop overrides, otherwise detect from the browser's User-Agent."""
    try:
        forced = str(st.query_params.get("layout", "")).lower()
        if forced in ("mobile", "desktop"):
            return forced == "mobile"
        ua = (st.context.headers.get("User-Agent") or "").lower()
        return any(k in ua for k in ("iphone", "android", "ipod", "mobile"))
    except Exception:  # noqa: BLE001
        return False


def _ask_list(questions: list, limit: int) -> list:
    """Questions to show (dicts with label/question/needs_review), dropping near-duplicate wording."""
    out, seen = [], []
    for q in questions:
        q = q if isinstance(q, dict) else {"question": str(q)}
        words = set(re.sub(r"[^\w\s]", " ", str(q.get("question", "")).lower()).split())
        if any(len(words & s) / max(1, len(words | s)) >= 0.7 for s in seen):
            continue
        seen.append(words)
        out.append(q)
        if len(out) >= limit:
            break
    return out


# criterion id -> booking field, used when the configuration does not set `booking_field` itself
BOOKING_FOR = {"check_in_date": "check_in_date", "check_out_date": "check_out_date", "number_of_nights": "nights", "number_of_rooms": "rooms",
               "primary_guest_name": "customer_name", "customer_name": "customer_name", "room_type": "room_type", "room_rate": "room_rate",
               "total_estimated_cost": "total_estimated_cost", "contact_number": "contact_number", "email_address": "email"}


def _transcript_html(s, interim) -> str:
    rows = "".join(
        f'<div class="seg{" alt" if i % 2 else ""}"><span class="ts">[{fmt_ts(g.start)}]</span>'
        f'<span class="sp">{e(g.speaker)}:</span><br>{e(g.text)}</div>' for i, g in enumerate(s.segments))
    if interim:
        rows += f'<div class="seg" style="opacity:.6"><span class="sp">{e(interim["speaker"])}:</span><br><i>{e(interim["text"])}</i></div>'
    if not rows:
        rows = '<div style="color:#6b7280">Transcript will appear here as people speak…</div>'
    return f'<div class="card"><div class="sec">LIVE TRANSCRIPT</div><div class="tr">{rows}</div></div>'


def _booking_rows(d: dict) -> dict:
    """Known booking values keyed by field (the 'Not discussed' rows are dropped)."""
    return {r["key"]: r for sec in d["report"]["sections"] for r in sec["rows"] if r["known"]}


def _snapshot(brow: dict) -> str:
    """One line of what is known so far: guest, dates, rooms."""
    def v(k):
        r = brow.get(k)
        if not r:
            return ""
        return e(r["value"]) + ("" if r["confirmed"] else ' <span class="u">(to confirm)</span>')

    parts = []
    if v("customer_name"):
        parts.append("👤 " + v("customer_name"))
    if v("check_in_date") or v("check_out_date"):
        parts.append("📅 " + (v("check_in_date") or "?") + " → " + (v("check_out_date") or "?")
                     + (f" ({e(brow['nights']['value'])} nights)" if brow.get("nights") else ""))
    elif brow.get("nights"):
        parts.append(f"📅 {e(brow['nights']['value'])} nights")
    if v("rooms"):
        parts.append("🛏 " + v("rooms") + " room(s)" + (f" · {v('room_type')}" if v("room_type") else ""))
    elif v("room_type"):
        parts.append("🛏 " + v("room_type"))
    if v("contact_number"):
        parts.append("📞 " + v("contact_number"))
    return " · ".join(parts)


def _checklist_html(cov: dict, brow: dict, config: dict) -> str:
    """Topic coverage and booking values in ONE list: each criterion shows its captured value."""
    bf = {c["id"]: c.get("booking_field") for tp in config.get("topics", []) for c in tp.get("criteria", [])}
    used = set()
    out = ""
    for t in cov["topics"]:
        items = []
        for c in t["criteria"]:
            key = bf.get(c["id"]) or BOOKING_FOR.get(c["id"])
            row = brow.get(key) if key else None
            ev = c.get("evidence") or {}
            value = row["value"] if row else ""
            if row:
                used.add(key)
            elif c["status"] == "MET" and ev.get("text"):
                value = "“" + ev["text"][:70] + ("…" if len(ev["text"]) > 70 else "") + "”"
            confirm = c["status"] == "NEEDS_REVIEW" or bool(row and not row["confirmed"] and c["status"] != "MET")
            prev = row["previous"][-1] if row and row.get("previous") else ""
            items.append((c, value, confirm, prev, ev))
        status = t["status"]
        if status == "Complete":  # finished topics shrink to one line
            line = " · ".join(f'{e(c["label"])}' + (f': <b>{e(v)}</b>' if v else "") for c, v, _, _, _ in items)
            body = f'<div class="line">✓ {line}</div>'
        elif status == "Not Started":
            body = '<div class="chips">' + "".join(f'<span class="chip">○ {e(c["label"])}</span>' for c, *_ in items) + "</div>"
        else:
            body = ""
            for c, value, confirm, prev, ev in items:
                icon, cls = ("✓", "ok") if c["status"] == "MET" else ("!", "rv") if c["status"] == "NEEDS_REVIEW" else ("○", "no")
                tip = f' title="{e(ev.get("text", ""))} (heard {e(ev.get("timestamp", ""))})"' if ev.get("text") else ""
                body += (f'<div class="crit"{tip}><span class="{cls}">{icon}</span><span class="cl">{e(c["label"])}</span>'
                         + (f'<span class="cv">{e(value)}</span>' if value else "")
                         + (f'<span class="cp">was {e(prev)}</span>' if prev else "")
                         + ('<span class="cf">to confirm</span>' if confirm else "") + "</div>")
        out += (f'<div class="topic"><div class="row"><span>{e(t["name"])}</span><span>{e(t["display"])}</span></div>'
                f'<div class="bar"><i style="width:{t["percent"] or 0}%"></i></div>'
                f'<div class="sub"><span>{t["met"]} of {t["applicable"]} criteria completed</span><span class="tag">{e(status)}</span></div>{body}</div>')
    extra = [r for k, r in brow.items() if k not in used and k not in ("customer_name",)]
    if extra:
        out += ('<div class="topic"><div class="sub"><span>Also captured</span></div><div class="chips">'
                + "".join(f'<span class="chip m">{e(r["label"])}: {e(r["value"])}</span>' for r in extra) + "</div></div>")
    return f'<div class="card"><div class="sec">CHECKLIST</div>{out}</div>'


def _next_action_html(d: dict, s) -> str:
    """ONE card for what to do next: questions (confirmations first), booking status, follow-up when finished."""
    chip = f'<span class="pill gray">{e(s.booking_status)}</span>' if s.booking_status != "Unknown" else ""
    if s.finalized:
        text = e(s.next_action or "Review the conversation and follow up with the customer.")
        return f'<div class="card nba"><div class="top"><span class="sec">FOLLOW-UP</span>{chip}</div><div class="q">{text}</div></div>'
    qs = _ask_list(d["questions"], 3)
    if qs:
        body = "".join(f'<div class="q{" review" if q.get("needs_review") else ""}"><span class="tg">{e(q.get("label", "Ask"))}</span>{e(q["question"])}</div>' for q in qs)
    else:
        body = f'<div class="q">{e(s.next_action) if s.next_action else "All checklist items are covered ✓"}</div>'
    return f'<div class="card nba"><div class="top"><span class="sec">NEXT BEST ACTION</span>{chip}</div>{body}</div>'


def _summary_box(s, mobile: bool) -> None:
    if not (s.summary or s.finalized):
        return  # nothing useful to say yet
    with st.expander("Final report" if s.finalized else "Call summary", expanded=s.finalized or not mobile):
        st.write(s.summary or "No summary available.")
        if s.finalized:
            fr = report_service.final_report(s)
            st.markdown(f"**Outcome:** {fr['outcome']}")
            if fr["missing_information"]:
                st.markdown("**Missing information**\n" + "\n".join(f"- {x}" for x in fr["missing_information"]))
            if fr["questions_not_asked"]:
                st.markdown("**Questions not asked**\n" + "\n".join(f"- {x}" for x in fr["questions_not_asked"]))


def render_live(session, interim=None, elapsed: float = 0.0) -> None:
    """Draw the report for the session's current state (called repeatedly while processing).

    Every fact appears once. Desktop: transcript left, checklist right. Phone: checklist first, transcript last."""
    s = session
    mobile = is_mobile()
    d = report_service.dashboard(s)
    cov = d["coverage"]
    ov = cov["overall"]
    brow = _booking_rows(d)
    status = s.status.value
    pill = {"COMPLETED": ("Completed", "green"), "FAILED": ("Failed", "red")}.get(status, (status.title(), "amber"))
    final = ("Final results", "green") if s.finalized else ("Live provisional analysis", "amber")
    last = s.segments[-1].start if s.segments else 0
    snap = _snapshot(brow)
    st.markdown(
        f'<div class="card hdr"><b>{e(s.name)}</b><span class="cfg">{e(s.config.get("name", ""))}</span>'
        f'<span class="time">{fmt_ts(max(s.duration_s, last, elapsed))}</span>'
        f'<span class="pill {pill[1]}">{e(pill[0])}</span><span class="pill {final[1]}">{final[0]}</span>'
        + (f'<div class="snap">{snap}</div>' if snap else "") + "</div>"
        f'<div class="card"><span style="font-size:19px">Overall Coverage: <b>{e(ov["display"])}</b> '
        f'<span style="color:#6b7280">{ov["met"]} of {ov["applicable"]} applicable criteria met</span></span>'
        f'<div class="bar"><i style="width:{ov["percent"] or 0}%"></i></div></div>' + _next_action_html(d, s),
        unsafe_allow_html=True,
    )
    if s.error:
        st.error(s.error)
    for w in s.warnings:
        st.warning(w)

    checklist = _checklist_html(cov, brow, s.config)
    if mobile:
        st.markdown(checklist, unsafe_allow_html=True)
        _summary_box(s, True)
        st.markdown(_transcript_html(s, interim), unsafe_allow_html=True)
    else:
        left, right = st.columns(2)
        with left:
            st.markdown(_transcript_html(s, interim), unsafe_allow_html=True)
        with right:
            st.markdown(checklist, unsafe_allow_html=True)
        _summary_box(s, False)

    runs = (s.timings or {}).get("analysis", [])
    if str(st.query_params.get("debug", "")) and (runs or "first_transcript_ms" in (s.timings or {})):  # diagnostics: add ?debug=1
        lr = runs[-1] if runs else {}
        st.caption(f"⏱ speech→first transcript: {(s.timings or {}).get('first_transcript_ms', '–')} ms · "
                   f"last analysis: waited {lr.get('segments_to_request_ms', '–')} ms, Gemini took {lr.get('request_ms', '–')} ms · "
                   f"{len(runs)} analysis run(s)")


async def run_live(mgr: SessionManager, sid: str, view) -> None:
    """Run the pipeline and redraw the report every second so it fills in live on this same page."""
    rt = mgr.runtime(sid)
    task = asyncio.create_task(rt.process_upload())
    while not task.done():
        with view.container():
            render_live(rt.session)
        await asyncio.sleep(1.0)
    await task
    with view.container():
        render_live(rt.session)


def start(mgr: SessionManager, name: str, cfg: dict, language: str, filename: str, data: bytes):
    mime, err = validate_audio(filename, data, mgr.settings.max_upload_mb)
    if err:
        st.error(err)
        return None
    s = mgr.create(name or filename, cfg, language, "upload", True)
    d = mgr.store.session_dir(s.id)
    d.mkdir(parents=True, exist_ok=True)
    ext = "." + filename.rsplit(".", 1)[-1].lower()
    (d / f"audio{ext}").write_bytes(data)
    s.audio_file, s.audio_mime = f"audio{ext}", mime
    s.duration_s = 0.0
    mgr.store.save(s)
    return s.id


def session_form(key: str, configs: list):
    cfg = st.selectbox("Configuration", configs, format_func=lambda c: c.get("name", c["id"]), key=f"cfg_{key}")
    c1, c2 = st.columns(2)
    name = c1.text_input("Session name", key=f"name_{key}")
    dl = cfg.get("language")
    language = c2.selectbox("Language", LANGUAGES, index=LANGUAGES.index(dl) if dl in LANGUAGES else 0, key=f"lang_{key}")
    consent = st.checkbox("I confirm the participants consented to this recording.", key=f"consent_{key}")
    return cfg, name, language, consent


def show_saved(mgr: SessionManager, sid: str) -> None:
    rt = mgr.runtime(sid)
    if not rt:
        st.error("Session not found.")
        return
    s = rt.session
    audio = mgr.store.session_dir(sid) / s.audio_file if s.audio_file else None
    if audio and audio.exists():
        st.audio(audio.read_bytes(), format=s.audio_mime or "audio/wav")
    render_live(s)
    if s.finalized or s.segments:
        st.download_button("Download HTML report", report_service.export_html(s, audio, mgr.settings.embed_audio_max_mb),
                           file_name=f"sales_assist_{sid}.html", mime="text/html", key=f"dl_{sid}")


def tab_live(mgr: SessionManager, configs: list) -> None:
    if webrtc_streamer is None:
        st.error("Live microphone needs the `streamlit-webrtc` package, which is not installed in this environment. "
                 "Reboot the app on Streamlit Cloud (Manage app → Reboot) and check the install log; use Upload Recording meanwhile.")
        return
    h, loop = live_holder(), bg_loop()
    cfg, name, language, consent = session_form("live", configs)
    st.caption("Press START and allow the microphone — the report updates while people speak. Press STOP to finalize.")
    with st.container(key="live_ctrl"):  # floats at the bottom on phones so STOP is always reachable
        ctx = webrtc_streamer(key="sales-assist-live", mode=WebRtcMode.SENDONLY, audio_frame_callback=on_audio,
                              media_stream_constraints={"audio": True, "video": False}, rtc_configuration=RTC)
    playing = bool(ctx.state.playing)
    if playing and h["rt"] is None:  # mic just started -> open a live session
        if not consent:
            st.warning("Tick the consent box, then press STOP and START again.")
        else:
            s = mgr.create(name, cfg, language, "live", True)
            h["rs"], h["rt"] = None, mgr.runtime(s.id)
            asyncio.run_coroutine_threadsafe(h["rt"].start_live(), loop)
            st.session_state["live_sid"] = s.id
    elif not playing and h["rt"] is not None:  # mic stopped -> finalize the report
        rt, h["rt"] = h["rt"], None
        with st.spinner("Finalizing report…"):
            try:
                asyncio.run_coroutine_threadsafe(rt.stop_live(), loop).result(180)
            except Exception as ex:  # noqa: BLE001
                st.error(f"Could not finalize: {ex}")
    if playing and h["rt"] is not None:  # live view: redraw every second while speaking
        rt = h["rt"]
        view = st.empty()
        while ctx.state.playing:
            with view.container():
                render_live(rt.session, rt.interim, time.monotonic() - rt._t_start if rt._t_start else 0)
            time.sleep(1.0)
        st.rerun()
    elif st.session_state.get("live_sid"):
        show_saved(mgr, st.session_state["live_sid"])
    else:
        st.info("Press START to begin a live session.")


def tab_upload(mgr: SessionManager, configs: list) -> None:
    cfg, name, language, consent = session_form("upload", configs)
    up = st.file_uploader("Upload recording", type=["wav", "mp3", "aiff", "aif", "aac", "ogg", "flac"])
    if st.button("Start analysis", type="primary", disabled=not consent or not up):
        sid = start(mgr, name, cfg, language, up.name, up.getvalue())
        if sid:
            st.session_state["upload_sid"] = sid
            rt = mgr.runtime(sid)
            audio = mgr.store.session_dir(sid) / rt.session.audio_file
            st.audio(audio.read_bytes(), format=rt.session.audio_mime or "audio/wav")
            view = st.empty()
            asyncio.run(run_live(mgr, sid, view))  # report fills in live, right here
            mgr.runtimes.pop(sid, None)  # reload from disk next time (fresh event-loop state)
            return
    if st.session_state.get("upload_sid"):
        show_saved(mgr, st.session_state["upload_sid"])
    else:
        st.info("Choose a recording and press Start analysis — the report fills in live on this page.")


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _blank_crit() -> dict:
    return {"_k": _uid(), "label": "", "instruction": "", "question": "", "priority": "medium"}


def _blank_topic() -> dict:
    return {"_k": _uid(), "name": "", "criteria": [_blank_crit()]}


def _draft_from(cfg: dict | None) -> dict:
    d = copy.deepcopy(cfg) if cfg else {"id": "", "name": "", "description": "", "roles": ["Hotel Booking Agent", "Customer"],
                                        "language": "English", "topics": [_blank_topic()]}
    for tp in d["topics"]:
        tp["_k"] = _uid()
        for c in tp["criteria"]:
            c["_k"] = _uid()
    return d


def tab_configs(mgr: SessionManager, configs: list) -> None:
    ss = st.session_state
    if "cfg_sel_next" in ss:  # set by save/new before the selectbox is created
        ss["cfg_sel"] = ss.pop("cfg_sel_next")
    ids = [c["id"] for c in configs]
    names = {c["id"]: c.get("name", c["id"]) for c in configs}
    sel = st.selectbox("Configuration to edit", ids + ["__new__"], key="cfg_sel",
                       format_func=lambda i: "➕ New configuration" if i == "__new__" else names[i])
    if ss.get("cfg_draft_for") != sel:  # selection changed -> load a fresh draft
        ss["cfg_draft"] = _draft_from(None if sel == "__new__" else mgr.configs.get(sel))
        ss["cfg_draft_for"] = sel
    d = ss["cfg_draft"]
    k = ss["cfg_draft_for"]

    st.markdown("### Edit Configuration" if sel != "__new__" else "### New Configuration")
    d["name"] = st.text_input("Name", d["name"], key=f"n_{k}")
    d["description"] = st.text_input("Description", d.get("description", ""), key=f"d_{k}")
    roles = st.text_input("Roles (comma separated)", ", ".join(d.get("roles", [])), key=f"r_{k}")
    d["roles"] = [r.strip() for r in roles.split(",") if r.strip()]
    lang = d.get("language", "English")
    d["language"] = st.selectbox("Language", LANGUAGES, index=LANGUAGES.index(lang) if lang in LANGUAGES else 0, key=f"l_{k}")

    action = None
    for ti, tp in enumerate(d["topics"]):
        with st.container(border=True):
            tp["name"] = st.text_input(f"Topic {ti + 1} name", tp["name"], key=f"tn_{tp['_k']}")
            for c in tp["criteria"]:
                c1, c2, c3 = st.columns([3, 6, 1], vertical_alignment="bottom")
                c["label"] = c1.text_input("Criterion", c["label"], key=f"cl_{c['_k']}", label_visibility="collapsed", placeholder="Criterion name")
                c["instruction"] = c2.text_input("Instruction", c.get("instruction", ""), key=f"ci_{c['_k']}", label_visibility="collapsed",
                                                 placeholder="When is this criterion met?")
                if c3.button("Remove", key=f"rm_{c['_k']}"):
                    action = ("rm_crit", ti, c["_k"])
            b1, b2, _ = st.columns([1.2, 1.2, 8])
            if b1.button("Add criterion", key=f"ac_{tp['_k']}"):
                action = ("add_crit", ti)
            if b2.button("Delete topic", key=f"dt_{tp['_k']}"):
                action = ("del_topic", ti)
    if st.button("Add topic", key=f"at_{k}"):
        action = ("add_topic",)

    if action:
        if action[0] == "rm_crit":
            d["topics"][action[1]]["criteria"] = [c for c in d["topics"][action[1]]["criteria"] if c["_k"] != action[2]]
        elif action[0] == "add_crit":
            d["topics"][action[1]]["criteria"].append(_blank_crit())
        elif action[0] == "del_topic":
            d["topics"].pop(action[1])
        else:
            d["topics"].append(_blank_topic())
        st.rerun()

    s1, s2, _ = st.columns([1.5, 1.5, 7])
    if s1.button("Save configuration", type="primary", key=f"sv_{k}"):
        clean = copy.deepcopy(d)
        clean["topics"] = [tp for tp in clean["topics"] if tp["name"].strip() or any(c["label"].strip() for c in tp["criteria"])]
        for tp in clean["topics"]:
            tp["criteria"] = [c for c in tp["criteria"] if c["label"].strip()]
            tp.pop("_k", None)
            for c in tp["criteria"]:
                c.pop("_k", None)
        try:
            cfg = Configuration(**clean).model_dump()
            if sel == "__new__" and cfg["id"] in ids:
                cfg["id"] = f"{cfg['id']}_{_uid()[:4]}"
            mgr.configs.save(cfg)
        except ValidationError as ex:
            st.error("; ".join(x["msg"].replace("Value error, ", "") for x in ex.errors()))
        else:
            ss["cfg_sel_next"] = cfg["id"]
            ss.pop("cfg_draft_for", None)
            st.success("Configuration saved.")
            st.rerun()
    if sel != "__new__" and len(ids) > 1 and s2.button("Delete configuration", key=f"dc_{k}"):
        mgr.configs.delete(sel)
        ss["cfg_sel_next"] = next(i for i in ids if i != sel)
        ss.pop("cfg_draft_for", None)
        st.rerun()


def tab_recent(mgr: SessionManager) -> None:
    sessions = mgr.list()
    if not sessions:
        st.info("No sessions yet.")
        return
    for x in sessions:
        c1, c2, c3 = st.columns([6, 1, 1])
        c1.markdown(f"**{x.name}** · {x.config.get('name', '')} · {x.status.value.title()} · {x.overall_display or '0%'}")
        if c2.button("Open", key=f"open_{x.id}"):
            st.session_state["recent_sid"] = x.id
        if c3.button("Delete", key=f"del_{x.id}"):
            asyncio.run(mgr.delete(x.id))
            st.session_state.pop("recent_sid", None)
            st.rerun()
    if st.session_state.get("recent_sid"):
        st.divider()
        show_saved(mgr, st.session_state["recent_sid"])


def main() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    mgr = get_mgr()
    st.markdown("## <span style='color:#1d4ed8'>Sales Assist</span> <span class='mobile-sub' style='font-size:16px;color:#4b5563;font-weight:400'>Hotel Booking Conversation Assistant</span>", unsafe_allow_html=True)
    if not mgr.settings.configured:
        st.error("No GOOGLE_API_KEY / GEMINI_API_KEY found. Add it to Streamlit secrets (or .env locally).")
        st.stop()
    configs = mgr.configs.list()
    if not configs:
        st.error("No conversation configuration found.")
        st.stop()
    t1, t2, t3, t4 = st.tabs(["Live Session", "Upload Recording", "Configurations", "Recent Sessions"])
    with t1:
        tab_live(mgr, configs)
    with t2:
        tab_upload(mgr, configs)
    with t3:
        tab_configs(mgr, configs)
    with t4:
        tab_recent(mgr)


main()
