"""Session lifecycle: coordinates transcription, analysis scheduling, merging, persistence and UI events."""
import asyncio
import copy
import logging
import re
import time
import uuid
from datetime import datetime
from typing import Awaitable, Callable, Optional

from app.config import Settings
from app.models.session import Segment, Session, SessionStatus
from app.services import report_service
from app.services.analysis_merge import apply_speaker_role, init_criteria, merge_analysis
from app.services.analysis_scheduler import AnalysisScheduler
from app.services.coverage_engine import compute_coverage
from app.services.gemini_analysis import GeminiAnalysis
from app.services.gemini_client import GeminiClient, GeminiServiceError
from app.services.gemini_transcription import LiveTranscriber, transcribe_file
from app.utils.storage import ConfigStore, SessionStore
from app.services.audio_service import pcm_to_wav
from app.utils.validation import redact_cards

log = logging.getLogger("repassist.session")

ACTIVE = {SessionStatus.CONNECTING, SessionStatus.RECORDING, SessionStatus.PROCESSING, SessionStatus.FINALIZING}
CHUNK = 20  # transcript segments per progressive analysis section (uploads)
Send = Callable[[dict], Awaitable[None]]


def neutral_label(key: str) -> str:
    m = re.fullmatch(r"(?:spk|speaker)[_ ]?(\d+)", key or "", re.I)
    if m:
        return f"Speaker {m.group(1)}"
    return "Unknown" if not key or key == "unknown" else key.replace("_", " ").title()


class SessionRuntime:
    def __init__(self, session: Session, manager: "SessionManager"):
        self.session = session
        self.m = manager
        self.subscribers: set = set()
        self.transcriber: Optional[LiveTranscriber] = None
        self.scheduler: Optional[AnalysisScheduler] = None
        self.interim: Optional[dict] = None
        self.steps: list = []
        self._lock = asyncio.Lock()  # one analysis in flight
        self._finalizing = False
        self._t_start: Optional[float] = None
        self._unevaluated_since: Optional[float] = None
        self._pcm = None
        self._tasks: set = set()

    # ---------------- events ----------------
    async def broadcast(self, event: dict) -> None:
        for send in list(self.subscribers):
            try:
                await send(event)
            except Exception:  # noqa: BLE001
                self.subscribers.discard(send)

    def snapshot(self) -> dict:
        return {
            "type": "snapshot",
            "dashboard": report_service.dashboard(self.session),
            "segments": [s.model_dump() for s in self.session.segments],
            "interim": self.interim,
            "steps": self.steps,
        }

    async def attach(self, send: Send) -> None:
        await send(self.snapshot())
        self.subscribers.add(send)

    def detach(self, send: Send) -> None:
        self.subscribers.discard(send)

    async def emit_dashboard(self) -> None:
        s = self.session
        s.overall_display = compute_coverage(s.config, report_service.states_for_engine(s))["overall"]["display"]
        await self.broadcast({"type": "dashboard", "dashboard": report_service.dashboard(s)})

    async def set_status(self, status: SessionStatus) -> None:
        self.session.status = status
        await self.broadcast({"type": "status", "status": status.value})

    async def warn(self, msg: str) -> None:
        s = self.session
        if not s.warnings or s.warnings[-1] != msg:
            s.warnings.append(msg)
        await self.broadcast({"type": "warning", "message": msg})

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def save(self) -> None:
        self.m.store.save(self.session)

    # ---------------- segments ----------------
    def _display_speaker(self, key: str) -> str:
        s = self.session
        if key in s.speaker_map:
            return s.speaker_map[key]
        for seg in s.segments:
            if seg.speaker_key == key:
                return seg.speaker
        return neutral_label(key)

    def add_segment(self, key: str, text: str, start: float, end: Optional[float] = None, label: Optional[str] = None) -> Segment:
        s = self.session
        seg = Segment(id=f"seg_{len(s.segments) + 1:03d}", speaker_key=key, speaker=self._display_speaker(key) if not label else label,
                      text=redact_cards(text), start=round(start, 2), end=round(end, 2) if end is not None else None)
        s.segments.append(seg)
        return seg

    # ---------------- live ----------------
    async def start_live(self) -> None:
        s = self.session
        if self.transcriber or s.mode != "live":
            return
        await self.set_status(SessionStatus.CONNECTING)
        self._t_start = time.monotonic()
        self.scheduler = AnalysisScheduler(self.run_analysis, self.m.settings.debounce_s, self.m.settings.max_wait_s)
        self.scheduler.start()
        self.transcriber = LiveTranscriber(self.m.gemini, s.language, self._on_transcript, self._on_notice)
        self.transcriber.start()
        if self.m.settings.save_live_audio and self.m.store.persist:
            d = self.m.store.session_dir(s.id)
            d.mkdir(parents=True, exist_ok=True)
            self._pcm = open(d / "live.pcm", "wb")

    def push_audio(self, pcm: bytes) -> None:
        if self.transcriber and not self._finalizing:
            self.transcriber.push_audio(pcm)
            if self._pcm:
                self._pcm.write(pcm)

    async def _on_notice(self, level: str, msg: str) -> None:
        s = self.session
        if level == "connected":
            if s.status == SessionStatus.CONNECTING:
                await self.set_status(SessionStatus.RECORDING)
            await self.broadcast({"type": "connection", "state": "connected"})
        elif level == "warning":
            await self.broadcast({"type": "connection", "state": "reconnecting"})
            await self.warn(msg)
        elif level == "error":
            s.error = msg
            await self.broadcast({"type": "error", "message": msg})
            if not self._finalizing:
                self._spawn(self.stop_live("error"))

    async def _on_transcript(self, kind: str, p: dict) -> None:
        s = self.session
        if kind == "interim":
            self.interim = {"speaker": self._display_speaker(p["speaker_key"]), "speaker_key": p["speaker_key"],
                            "text": redact_cards(p["text"]), "start": round(p.get("start") or 0, 2)}
            await self.broadcast({"type": "interim", "segment": self.interim})
            return
        self.interim = None
        seg = self.add_segment(p["speaker_key"], p["text"], p["start"], p.get("end"))
        if "first_transcript_ms" not in s.timings and self.transcriber and self.transcriber.first_audio_t:
            s.timings["first_transcript_ms"] = int((time.monotonic() - self.transcriber.first_audio_t) * 1000)
        if self._unevaluated_since is None:
            self._unevaluated_since = time.monotonic()
        self.save()
        await self.broadcast({"type": "segment", "segment": seg.model_dump()})
        if self.scheduler:
            self.scheduler.notify(seg.text)

    async def stop_live(self, reason: str = "user") -> None:
        if self._finalizing:
            return
        self._finalizing = True
        s = self.session
        await self.set_status(SessionStatus.FINALIZING)
        if self.transcriber:
            await self.transcriber.stop()
        if self._t_start:
            s.duration_s = round(time.monotonic() - self._t_start, 1)
        self._close_pcm()
        await self._finalize()

    def _close_pcm(self) -> None:
        if not self._pcm:
            return
        s = self.session
        d = self.m.store.session_dir(s.id)
        try:
            self._pcm.close()
            pcm_to_wav(d / "live.pcm", d / "audio.wav")
            s.audio_file, s.audio_mime = "audio.wav", "audio/wav"
        except Exception:  # noqa: BLE001
            log.warning("Could not store live audio")
        finally:
            self._pcm = None
            (d / "live.pcm").unlink(missing_ok=True)

    async def abort(self) -> None:
        """Cancel everything (used when a session is deleted mid-flight)."""
        self._finalizing = True
        if self.scheduler:
            await self.scheduler.stop()
        if self.transcriber:
            self.transcriber.stopping = True
            if self.transcriber._task:
                self.transcriber._task.cancel()
        for t in list(self._tasks):
            t.cancel()
        if self._pcm:
            try:
                self._pcm.close()
            except Exception:  # noqa: BLE001
                pass
            self._pcm = None

    # ---------------- analysis ----------------
    async def run_analysis(self, final: bool = False, max_new: Optional[int] = None) -> bool:
        """One analysis pass over *new* transcript content. Returns True on success."""
        s = self.session
        async with self._lock:
            total = len(s.segments)
            full = final or (s.analysis_count > 0 and s.analysis_count % 10 == 0 and total <= 300)  # periodic reconciliation
            if full:
                new, ctx, end = list(s.segments), [], total
            else:
                if s.evaluated_through >= total:
                    return True
                end = total if not max_new else min(total, s.evaluated_through + max_new)
                new = s.segments[s.evaluated_through:end]
                ctx = s.segments[max(0, s.evaluated_through - 6):s.evaluated_through]
            s.revision += 1
            rev = s.revision
            t_req = time.monotonic()
            queued_ms = int((t_req - self._unevaluated_since) * 1000) if self._unevaluated_since else 0
            self._unevaluated_since = None
            await self.broadcast({"type": "analysis_started", "revision": rev})
            try:
                result = await self.m.analysis.analyze(s, new, ctx, final=final)
            except GeminiServiceError as e:
                if e.kind == "invalid_response":
                    await self.warn("Analysis returned an invalid response; previous results were preserved.")
                else:
                    await self.warn(e.message)
                    await self.broadcast({"type": "service_error", "kind": e.kind, "message": e.message})
                await self.broadcast({"type": "analysis_done", "revision": rev, "ok": False})
                return False
            rejected = merge_analysis(s, result, rev)
            s.analysis_count += 1
            s.evaluated_through = max(s.evaluated_through, end)
            if final and result.final_report:
                s.final_report = result.final_report.model_dump()
            t_done = time.monotonic()
            await self.emit_dashboard()
            diag = s.timings.setdefault("analysis", [])
            diag.append({"revision": rev, "segments_to_request_ms": queued_ms, "request_ms": int((t_done - t_req) * 1000),
                         "ui_update_ms": int((time.monotonic() - t_done) * 1000), "rejected": len(rejected), "full": full})
            del diag[:-20]
            self.save()
            await self.broadcast({"type": "analysis_done", "revision": rev, "ok": True})
            return True

    # ---------------- finalisation ----------------
    async def _finalize(self) -> None:
        s = self.session
        t0 = time.monotonic()
        self._finalizing = True
        await self.set_status(SessionStatus.FINALIZING)
        if self.scheduler:
            await self.scheduler.drain(20)
            await self.scheduler.stop()
        if not s.segments:
            await self.warn("No speech was detected, so no report was generated.")
            s.finalized = True
            await self.set_status(SessionStatus.FAILED if s.error else SessionStatus.COMPLETED)
            self.save()
            await self.emit_dashboard()
            await self.broadcast({"type": "finalized"})
            return
        await self.broadcast({"type": "steps", "steps": self._steps("finalize")})
        ok = await self.run_analysis(final=True)
        if not ok:
            s.error = "Final reconciliation failed. The transcript is saved - use Retry to finalize."
            await self.set_status(SessionStatus.FAILED)
            self.save()
            await self.emit_dashboard()
            self._finalizing = False
            return
        s.error = None
        s.finalized = True
        s.timings["finalization_ms"] = int((time.monotonic() - t0) * 1000)
        if s.mode == "upload" and not self.m.store.persist:
            self._drop_upload_audio()
        await self.set_status(SessionStatus.COMPLETED)
        self.save()
        await self.emit_dashboard()
        await self.broadcast({"type": "finalized"})
        self._finalizing = False

    async def retry(self) -> None:
        s = self.session
        if self._finalizing or s.status != SessionStatus.FAILED:
            return
        self._finalizing = True
        s.error = None
        if s.mode == "upload" and not s.segments:
            self._finalizing = False
            await self.process_upload()
        else:
            self.scheduler = None
            await self._finalize()

    # ---------------- upload ----------------
    def _steps(self, active: str, detail: str = "") -> list:
        order = [("upload", "File uploaded"), ("transcribe", "Transcribing audio with Gemini"),
                 ("analyze", "Analyzing conversation against the checklist"), ("finalize", "Final reconciliation and report")]
        out, seen_active = [], False
        for key, label in order:
            if key == active:
                state, seen_active = "active", True
            else:
                state = "pending" if seen_active else "done"
            out.append({"key": key, "label": label + (f" ({detail})" if detail and key == active else ""), "state": state})
        self.steps = out
        return out

    async def process_upload(self) -> None:
        s = self.session
        self._finalizing = True
        s.error = None
        await self.set_status(SessionStatus.PROCESSING)
        path = self.m.store.session_dir(s.id) / (s.audio_file or "")
        await self.broadcast({"type": "steps", "steps": self._steps("transcribe")})
        try:
            segs = await transcribe_file(self.m.gemini, path, s.audio_mime or "audio/wav", s.language, s.config.get("roles", []))
        except GeminiServiceError as e:
            s.error = e.message
            self.steps = []
            await self.set_status(SessionStatus.FAILED)
            self.save()
            await self.broadcast({"type": "error", "message": e.message})
            await self.emit_dashboard()
            self._finalizing = False
            return
        s.segments.clear()
        if not segs:
            await self.warn("No speech detected in the recording.")
            s.finalized = True
            self.steps = []
            await self.set_status(SessionStatus.COMPLETED)
            self.save()
            await self.emit_dashboard()
            await self.broadcast({"type": "finalized"})
            self._finalizing = False
            return
        roles_by_key: dict = {}
        for g in segs:
            roles_by_key.setdefault(g["speaker_key"], set()).add(g["role"])
        for g in segs:
            known = roles_by_key[g["speaker_key"]] - {None}
            role = next(iter(known)) if len(known) == 1 and None not in roles_by_key[g["speaker_key"]] else None
            self.add_segment(g["speaker_key"], g["text"], g["start"], g["end"], label=role or g["speaker_label"])
        s.duration_s = max(s.duration_s, s.segments[-1].start)
        s.evaluated_through = 0
        self._unevaluated_since = time.monotonic()
        self.save()
        await self.broadcast({"type": "segments_all", "segments": [x.model_dump() for x in s.segments]})
        n_chunks = (len(s.segments) + CHUNK - 1) // CHUNK
        for i in range(n_chunks):  # progressive analysis: dashboard updates after every section
            await self.broadcast({"type": "steps", "steps": self._steps("analyze", f"section {i + 1} of {n_chunks}")})
            if not await self.run_analysis(max_new=CHUNK):
                break
        await self._finalize()
        self.steps = []

    def _drop_upload_audio(self) -> None:
        s = self.session
        if s.audio_file:
            (self.m.store.session_dir(s.id) / s.audio_file).unlink(missing_ok=True)
            s.audio_file = None


class SessionManager:
    def __init__(self, settings: Settings, gemini: GeminiClient, store: SessionStore, configs: ConfigStore):
        self.settings = settings
        self.gemini = gemini
        self.store = store
        self.configs = configs
        self.analysis = GeminiAnalysis(gemini)
        self.runtimes: dict[str, SessionRuntime] = {}

    def create(self, name: str, config: dict, language: str, mode: str, consent: bool) -> Session:
        sid = uuid.uuid4().hex[:12]
        s = Session(id=sid, name=(name or "").strip()[:120] or f"Session {datetime.now():%Y-%m-%d %H:%M}",
                    language=language or config.get("language", "English"), mode=mode, config=copy.deepcopy(config), consent=consent)  # snapshot
        init_criteria(s)
        self.runtimes[sid] = SessionRuntime(s, self)
        self.store.save(s)
        return s

    def runtime(self, sid: str) -> Optional[SessionRuntime]:
        rt = self.runtimes.get(sid)
        if rt:
            return rt
        s = self.store.load(sid)
        if not s:
            return None
        if s.status in ACTIVE or s.status == SessionStatus.CREATED:  # server restarted mid-session
            s.status = SessionStatus.FAILED
            s.error = "Session was interrupted. The saved transcript is preserved."
            self.store.save(s)
        rt = SessionRuntime(s, self)
        self.runtimes[sid] = rt
        return rt

    def list(self) -> list[Session]:
        found = {s.id: s for s in self.store.list()}
        for sid, rt in self.runtimes.items():
            found[sid] = rt.session
        return sorted(found.values(), key=lambda s: s.created_at, reverse=True)

    async def delete(self, sid: str) -> bool:
        rt = self.runtimes.pop(sid, None)
        if rt:
            await rt.abort()
        removed = self.store.delete(sid)
        return removed or rt is not None
