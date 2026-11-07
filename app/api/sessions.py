from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from app.models.configuration import LANGUAGES
from app.models.session import SessionStatus
from app.services import report_service
from app.services.analysis_merge import apply_speaker_role
from app.services.gemini_client import MESSAGES
from app.utils.validation import is_safe_id, validate_audio

router = APIRouter(prefix="/api", tags=["sessions"])


def _mgr(request: Request):
    return request.app.state.mgr


def _runtime(request: Request, sid: str):
    if not is_safe_id(sid):
        raise HTTPException(404, "Session not found")
    rt = _mgr(request).runtime(sid)
    if not rt:
        raise HTTPException(404, "Session not found")
    return rt


def _require_key(request: Request):
    if not request.app.state.settings.configured:
        raise HTTPException(400, MESSAGES["missing_key"])


def _config_or_404(request: Request, cid: str) -> dict:
    cfg = request.app.state.configs.get(cid)
    if not cfg:
        raise HTTPException(404, "Conversation configuration not found")
    return cfg


@router.get("/health")
def health(request: Request):
    st = request.app.state.settings
    g = request.app.state.gemini
    return {
        "ai_ready": st.configured,  # only means a key is configured - NOT that it is valid or has quota
        "configured": st.configured,
        "transcription_status": g.status["transcription"],
        "analysis_status": g.status["analysis"],
        "models": {"live_transcription": st.live_model, "upload_transcription": st.upload_model, "analysis": st.analysis_model},
        "persistence": {"save_sessions": st.save_sessions, "save_live_audio": st.save_live_audio},
        "languages": LANGUAGES,
    }


@router.post("/connectivity-check")
async def connectivity(request: Request):
    return await request.app.state.gemini.check_connectivity()


class CreateSession(BaseModel):
    name: str = ""
    configuration_id: str
    language: Optional[str] = None
    consent: bool = False


def _check_inputs(consent: bool, language: Optional[str]):
    if not consent:
        raise HTTPException(400, "Recording consent must be confirmed before starting.")
    if language and language not in LANGUAGES:
        raise HTTPException(400, f"Language must be one of {LANGUAGES}")


@router.post("/sessions", status_code=201)
def create_session(body: CreateSession, request: Request):
    _check_inputs(body.consent, body.language)
    _require_key(request)
    cfg = _config_or_404(request, body.configuration_id)
    s = _mgr(request).create(body.name, cfg, body.language or cfg.get("language", "English"), "live", True)
    return report_service.session_info(s)


@router.post("/sessions/upload", status_code=201)
async def upload_session(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(""),
    configuration_id: str = Form(...),
    language: str = Form(""),
    consent: bool = Form(False),
    duration: float = Form(0.0),
):
    _check_inputs(consent, language or None)
    _require_key(request)
    cfg = _config_or_404(request, configuration_id)
    st = request.app.state.settings
    limit = int(st.max_upload_mb * 1024 * 1024)
    data = await file.read(limit + 1)
    mime, err = validate_audio(file.filename or "", data, st.max_upload_mb)
    if err:
        raise HTTPException(400, err)
    mgr = _mgr(request)
    s = mgr.create(name or (file.filename or ""), cfg, language or cfg.get("language", "English"), "upload", True)
    ext = "." + (file.filename or "audio.wav").rsplit(".", 1)[-1].lower()
    d = mgr.store.session_dir(s.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"audio{ext}").write_bytes(data)
    s.audio_file, s.audio_mime = f"audio{ext}", mime
    s.duration_s = max(0.0, min(duration, 24 * 3600))
    mgr.store.save(s)
    rt = mgr.runtime(s.id)
    await rt.set_status(SessionStatus.PROCESSING)
    rt._spawn(rt.process_upload())
    return report_service.session_info(s)


@router.get("/sessions")
def list_sessions(request: Request):
    out = []
    for s in _mgr(request).list():
        info = report_service.session_info(s)
        info["segment_count"] = len(s.segments)
        out.append(info)
    return {"sessions": out}


@router.get("/sessions/{sid}")
def get_session(sid: str, request: Request):
    return _runtime(request, sid).snapshot()


@router.delete("/sessions/{sid}")
async def delete_session(sid: str, request: Request):
    if not is_safe_id(sid) or not await _mgr(request).delete(sid):
        raise HTTPException(404, "Session not found")
    return {"deleted": sid}


@router.get("/sessions/{sid}/report")
def get_report(sid: str, request: Request):
    return report_service.final_report(_runtime(request, sid).session)


@router.get("/sessions/{sid}/export")
def export(sid: str, request: Request):
    mgr = _mgr(request)
    s = _runtime(request, sid).session
    audio = mgr.store.session_dir(sid) / s.audio_file if s.audio_file else None
    body = report_service.export_html(s, audio, request.app.state.settings.embed_audio_max_mb)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in s.name)[:50] or "session"
    return Response(body, media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="repassist_{safe}.html"'})


@router.post("/sessions/{sid}/retry")
async def retry(sid: str, request: Request):
    rt = _runtime(request, sid)
    if rt.session.status != SessionStatus.FAILED:
        raise HTTPException(400, "Only failed sessions can be retried.")
    _require_key(request)
    rt._spawn(rt.retry())
    return {"retrying": True}


class SpeakerBody(BaseModel):
    speaker_key: str
    role: str


@router.put("/sessions/{sid}/speakers")
async def set_speaker(sid: str, body: SpeakerBody, request: Request):
    rt = _runtime(request, sid)
    s = rt.session
    roles = s.config.get("roles", [])
    if body.role == "Unknown":
        s.speaker_map.pop(body.speaker_key, None)
        from app.services.session_service import neutral_label
        apply_speaker_role(s, body.speaker_key, neutral_label(body.speaker_key), manual=False)
    elif body.role in roles:
        apply_speaker_role(s, body.speaker_key, body.role, manual=True)
    else:
        raise HTTPException(400, f"Role must be one of {roles + ['Unknown']}")
    rt.save()
    await rt.broadcast({"type": "segments_all", "segments": [x.model_dump() for x in s.segments]})
    await rt.emit_dashboard()
    return {"ok": True, "speaker_map": s.speaker_map}


@router.get("/sessions/{sid}/audio")
def audio(sid: str, request: Request):
    mgr = _mgr(request)
    s = _runtime(request, sid).session
    if not s.audio_file:
        raise HTTPException(404, "No audio stored for this session")
    p = mgr.store.session_dir(sid) / s.audio_file
    if not p.exists():
        raise HTTPException(404, "Audio file not found")
    return FileResponse(p, media_type=s.audio_mime or "audio/wav")
