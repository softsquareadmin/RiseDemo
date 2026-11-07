import json
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.models.session import SessionStatus
from app.utils.validation import is_safe_id

router = APIRouter()


def _origin_ok(ws: WebSocket) -> bool:
    """Local-app CSRF guard: a browser Origin, when present, must match the Host header."""
    origin = ws.headers.get("origin")
    if not origin:
        return True
    return urlparse(origin).netloc == ws.headers.get("host", "")


@router.websocket("/ws/sessions/{sid}")
async def session_socket(ws: WebSocket, sid: str):
    if not is_safe_id(sid) or not _origin_ok(ws):
        await ws.close(code=1008)
        return
    mgr = ws.app.state.mgr
    rt = mgr.runtime(sid)
    if rt is None:
        await ws.close(code=1008)
        return
    await ws.accept()

    async def send(event: dict) -> None:
        await ws.send_json(event)

    await rt.attach(send)
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                rt.push_audio(msg["bytes"])  # ~100 ms PCM16 frames, streamed continuously
            elif msg.get("text"):
                try:
                    cmd = json.loads(msg["text"])
                except ValueError:
                    continue
                t = cmd.get("type")
                if t == "start":
                    if not mgr.settings.configured:
                        await send({"type": "error", "message": "No Gemini API key configured."})
                    else:
                        await rt.start_live()
                elif t == "stop":
                    rt._spawn(rt.stop_live("user"))
                elif t == "ping":
                    await send({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        rt.detach(send)
        # Browser closed/refreshed mid-recording: keep the transcript and finalize in the background.
        if rt.session.status in (SessionStatus.CONNECTING, SessionStatus.RECORDING) and not rt.subscribers:
            await rt.warn("Session interrupted (browser disconnected). Transcript was preserved.")
            rt._spawn(rt.stop_live("interrupted"))
