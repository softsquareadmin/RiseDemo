"""RepAssist - Hotel Booking Conversation Assistant (Streamlit). Run with:  streamlit run main.py"""
import asyncio
import os

import streamlit as st

# Streamlit Cloud secrets -> environment, before Settings() reads it.
for _k in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
    try:
        if _k in st.secrets and not os.getenv(_k):
            os.environ[_k] = str(st.secrets[_k])
    except Exception:  # noqa: BLE001  (no secrets file locally)
        pass

from app.config import Settings
from app.models.configuration import LANGUAGES
from app.services import report_service
from app.services.gemini_client import GeminiClient
from app.services.session_service import SessionManager
from app.utils.storage import ConfigStore, SessionStore
from app.utils.validation import validate_audio

st.set_page_config(page_title="RepAssist", page_icon="🏨", layout="wide")


@st.cache_resource
def get_mgr() -> SessionManager:
    s = Settings()
    configs = ConfigStore(s.configs_dir, s.templates_dir)
    configs.seed_defaults()
    gemini = GeminiClient(s)
    return SessionManager(s, gemini, SessionStore(s.sessions_dir, persist=s.save_sessions), configs)


def show_report(mgr: SessionManager, sid: str) -> None:
    rt = mgr.runtime(sid)
    if not rt:
        st.error("Session not found.")
        return
    s = rt.session
    d = report_service.dashboard(s)
    st.subheader(s.name)
    st.caption(f"{s.config.get('name', '')} · {s.language} · status: {s.status.value}")
    if s.error:
        st.error(s.error)
    for w in s.warnings:
        st.warning(w)
    overall = d["coverage"]["overall"]
    st.metric("Overall coverage", overall["display"])
    left, right = st.columns(2)
    with left:
        st.markdown("#### Summary")
        st.write(s.summary or "No summary available.")
        st.markdown(f"**Booking status:** {s.booking_status}  \n**Next action:** {s.next_action or '-'}")
        st.markdown("#### Booking details")
        for sec in d["report"]["sections"]:
            with st.expander(sec["title"], expanded=True):
                for r in sec["rows"]:
                    st.write(f"**{r['label']}:** {r['value']}" + (" ✅" if r["confirmed"] else ""))
    with right:
        st.markdown("#### Topic coverage")
        for t in d["coverage"]["topics"]:
            with st.expander(f"{t['name']} — {t['display']}"):
                for c in t["criteria"]:
                    st.write(f"• {c['label']} — *{c['status']}*")
        if d["questions"]:
            st.markdown("#### Suggested questions")
            for q in d["questions"]:
                st.write(f"• {q['question'] if isinstance(q, dict) and 'question' in q else q}")
        if d["report"]["outstanding"]:
            st.markdown("#### Outstanding")
            for o in d["report"]["outstanding"]:
                st.write(f"• {o['topic']}: {o['label']}")
    st.markdown("#### Transcript")
    for seg in s.segments:
        st.write(f"**{seg.speaker}** ({seg.start:.0f}s): {seg.text}")
    audio = mgr.store.session_dir(sid) / s.audio_file if s.audio_file else None
    st.download_button("Download HTML report", report_service.export_html(s, audio, mgr.settings.embed_audio_max_mb),
                       file_name=f"repassist_{sid}.html", mime="text/html")


def process(mgr: SessionManager, name: str, cfg: dict, language: str, filename: str, data: bytes) -> str | None:
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
    mgr.store.save(s)
    with st.spinner("Transcribing and analysing…"):
        asyncio.run(mgr.runtime(s.id).process_upload())
    mgr.runtimes.pop(s.id, None)  # reload from disk next time (fresh event-loop state)
    return s.id


def main() -> None:
    mgr = get_mgr()
    st.title("🏨 RepAssist")
    st.caption("Hotel booking conversation assistant — upload or record a call to get a booking report.")
    if not mgr.settings.configured:
        st.error("No GOOGLE_API_KEY / GEMINI_API_KEY found. Add it to Streamlit secrets (or .env locally).")
        st.stop()

    with st.sidebar:
        st.header("Past sessions")
        sessions = mgr.list()
        ids = [x.id for x in sessions]
        labels = {x.id: f"{x.name} ({x.status.value})" for x in sessions}
        picked = st.selectbox("Open", [None] + ids, format_func=lambda i: "— select —" if i is None else labels[i])
        if picked and st.button("Delete session"):
            asyncio.run(mgr.delete(picked))
            st.session_state.pop("sid", None)
            st.rerun()

    configs = mgr.configs.list()
    if not configs:
        st.error("No conversation configuration found.")
        st.stop()
    new_tab, view_tab = st.tabs(["New session", "Report"])
    with new_tab:
        cfg = st.selectbox("Configuration", configs, format_func=lambda c: c.get("name", c["id"]))
        c1, c2 = st.columns(2)
        name = c1.text_input("Session name")
        language = c2.selectbox("Language", LANGUAGES, index=LANGUAGES.index(cfg.get("language", "English")) if cfg.get("language") in LANGUAGES else 0)
        consent = st.checkbox("I confirm the participants consented to this recording.")
        up = st.file_uploader("Upload audio", type=["wav", "mp3", "aiff", "aif", "aac", "ogg", "flac"])
        rec = st.audio_input("…or record now")
        if st.button("Analyse", type="primary", disabled=not consent or not (up or rec)):
            f = up or rec
            st.session_state["sid"] = process(mgr, name, cfg, language, getattr(f, "name", "") or "recording.wav", f.getvalue())
    with view_tab:
        sid = picked or st.session_state.get("sid")
        if sid:
            show_report(mgr, sid)
        else:
            st.info("Analyse a recording or pick a past session from the sidebar.")


main()
