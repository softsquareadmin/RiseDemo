"""Gemini transcription: Live streaming (real time) and uploaded-file transcription.

Only Google Gemini is used. The Live API configuration differs between dedicated transcription
models and conversational Live models, so the config is built per model family (see _config).
"""
import asyncio
import collections
import json
import logging
import re
import time
from typing import Awaitable, Callable, Optional

from google.genai import types

from app.services.gemini_client import GeminiClient, GeminiServiceError
from app.utils.validation import redact_cards

log = logging.getLogger("repassist.transcription")

LANG_CODES = {"English": ["en-US"], "Tamil": ["ta-IN"], "English-Tamil (Mixed)": ["en-US", "ta-IN"]}
PCM_MIME = "audio/pcm;rate=16000"
BYTES_PER_SECOND = 16000 * 2


def _norm(t: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (t or "").lower()).split())


def _parse_offset(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(str(v).strip().rstrip("s"))
    except ValueError:
        return None


# --------------------------------------------------------------------------------------
# Pure logic: turns interim / incremental transcription events into final segments.
# --------------------------------------------------------------------------------------
class TranscriptAssembler:
    """Reconciles interim text with finalized text and prevents duplicate segments.

    Events returned are ("interim", {...}) or ("final", {...}).
    """

    def __init__(self, idle_s: float = 1.5, clock: Callable[[], float] = time.monotonic):
        self.idle_s = idle_s
        self.clock = clock
        self.t0 = clock()
        self._reset()
        self._last_final: Optional[tuple] = None  # (speaker_key, normalized text)

    def _reset(self) -> None:
        self.buf = ""
        self.interim = ""
        self.spk = "unknown"
        self.start: Optional[float] = None
        self.last_event = self.clock()

    def _pending(self) -> str:
        return (self.buf or self.interim).strip()

    def _touch(self, speaker: Optional[str], offset: Optional[float]) -> list:
        events = []
        spk = speaker or "unknown"
        if self._pending() and spk != self.spk and speaker is not None and self.spk != "unknown":
            events += self._finalize()  # speaker changed -> close previous utterance
        if self.start is None:
            self.start = offset if offset is not None else self.clock() - self.t0
        self.spk = spk if speaker is not None or self.spk == "unknown" else self.spk
        self.last_event = self.clock()
        return events

    def on_interim(self, text: str, speaker: Optional[str] = None, offset: Optional[float] = None) -> list:
        if not text:
            return []
        events = self._touch(speaker, offset)
        self.interim = text
        events.append(("interim", {"speaker_key": self.spk, "text": self._pending(), "start": self.start}))
        return events

    def on_input(self, text: str, finished: bool, speaker: Optional[str] = None, offset: Optional[float] = None) -> list:
        events = self._touch(speaker, offset)
        if text:
            if self.buf and text.startswith(self.buf):  # cumulative style
                self.buf = text
            else:
                self.buf += text
            self.interim = ""
        if finished:
            events += self._finalize()
        elif self._pending():
            events.append(("interim", {"speaker_key": self.spk, "text": self._pending(), "start": self.start}))
        return events

    def on_turn_complete(self) -> list:
        return self._finalize()

    def tick(self) -> list:
        if self._pending() and self.clock() - self.last_event >= self.idle_s:
            return self._finalize()
        return []

    def flush(self) -> list:
        return self._finalize()

    def _finalize(self) -> list:
        text = " ".join(self._pending().split())
        spk, start = self.spk, self.start if self.start is not None else 0.0
        end = self.clock() - self.t0
        self._reset()
        if not text:
            return []
        key = (spk, _norm(text))
        if self._last_final == key:  # duplicate final -> drop
            return []
        self._last_final = key
        return [("final", {"speaker_key": spk, "text": text, "start": start, "end": end})]


# --------------------------------------------------------------------------------------
# Gemini Live streaming transcription
# --------------------------------------------------------------------------------------
class LiveTranscriber:
    FRAME_QUEUE_MAX = 100  # ~10 s of 100 ms frames buffered while reconnecting
    MAX_RECONNECTS = 5

    def __init__(
        self,
        gemini: GeminiClient,
        language: str,
        on_event: Callable[[str, dict], Awaitable[None]],
        on_notice: Callable[[str, str], Awaitable[None]],
    ):
        self.gemini = gemini
        self.language_codes = LANG_CODES.get(language, ["en-US"])
        self.on_event = on_event  # ("interim"|"final", payload)
        self.on_notice = on_notice  # (level, message): level = info|warning|error|connected
        self.assembler = TranscriptAssembler()
        self.queue: collections.deque = collections.deque()
        self.lost_bytes = 0
        self.connected = False
        self.stopping = False
        self.failed: Optional[GeminiServiceError] = None
        self.diarization = False  # Live API rejects diarization ("not supported in BidiGenerateContent")
        self.first_audio_t: Optional[float] = None
        self.first_transcript_t: Optional[float] = None
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    # -- audio in ------------------------------------------------------------------
    def push_audio(self, pcm: bytes) -> None:
        if self.stopping or not pcm:
            return
        if self.first_audio_t is None:
            self.first_audio_t = time.monotonic()
        if len(self.queue) >= self.FRAME_QUEUE_MAX:
            self.lost_bytes += len(self.queue.popleft())  # honest accounting of lost audio
        self.queue.append(pcm)
        self._wake.set()

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self, drain_s: float = 6.0) -> None:
        """Flush remaining audio, wait for pending final transcripts, close."""
        self.stopping = True
        self._wake.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=drain_s + 6)
            except asyncio.TimeoutError:
                self._task.cancel()
        await self._emit(self.assembler.flush())

    # -- internals -------------------------------------------------------------------
    def _config(self) -> types.LiveConnectConfig:
        model = self.gemini.settings.live_model.lower()
        if "transcribe" in model:
            # Dedicated transcription model: transcription config only, no model response.
            atc = types.AudioTranscriptionConfig(
                language_codes=self.language_codes,
                diarization=True if self.diarization else None,
            )
            return types.LiveConnectConfig(input_audio_transcription=atc)
        # Conversational Live model used only for its input transcription; the model never replies aloud.
        return types.LiveConnectConfig(
            response_modalities=[types.Modality.TEXT],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            system_instruction=types.Content(
                parts=[types.Part(text="You are a silent transcription device. Never respond to the speakers.")]
            ),
        )

    async def _emit(self, events: list) -> None:
        for kind, payload in events:
            if self.first_transcript_t is None:
                self.first_transcript_t = time.monotonic()
            await self.on_event(kind, payload)

    async def _run(self) -> None:
        attempt = 0
        while not self.stopping or self.queue:
            try:
                await self._session()
                if self.stopping:
                    return
                raise ConnectionError("Gemini Live connection closed")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                if self.diarization and "diariz" in str(e).lower():
                    self.diarization = False
                    await self.on_notice("warning", "This model rejected speaker diarization; continuing with neutral speaker labels.")
                    continue
                err = self.gemini.classify(e)
                self.connected = False
                if self.stopping and not self.queue:
                    return
                if not err.retryable or attempt >= self.MAX_RECONNECTS:
                    self.failed = err
                    self.gemini.mark("transcription", False, err)
                    await self.on_notice("error", err.message)
                    return
                attempt += 1
                await self.on_notice("warning", f"Connection to Gemini lost - reconnecting ({attempt}/{self.MAX_RECONNECTS}). Audio is buffered briefly.")
                await asyncio.sleep(min(2 ** attempt, 15))
            else:
                attempt = 0

    async def _session(self) -> None:
        client = self.gemini.client
        async with client.aio.live.connect(model=self.gemini.settings.live_model, config=self._config()) as sess:
            self.connected = True
            self.gemini.mark("transcription", True)
            lost = self.lost_bytes
            self.lost_bytes = 0
            await self.on_notice("connected", "")
            if lost:
                await self.on_notice("warning", f"About {lost / BYTES_PER_SECOND:.1f}s of audio was lost during the disconnection and was not transcribed.")
            send = asyncio.create_task(self._send_loop(sess))
            recv = asyncio.create_task(self._recv_loop(sess))
            tick = asyncio.create_task(self._tick_loop())
            try:
                done, _ = await asyncio.wait({send, recv}, return_when=asyncio.FIRST_COMPLETED)
                if recv in done:
                    recv.result()  # raises when the stream failed
                    if not self.stopping:
                        raise ConnectionError("stream ended")
                    return
                send.result()
                # clean stop: audio flushed; allow final transcripts to arrive
                try:
                    await asyncio.wait_for(asyncio.shield(recv), timeout=5.0)
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                    pass
            finally:
                for t in (send, recv, tick):
                    t.cancel()
                await asyncio.gather(send, recv, tick, return_exceptions=True)

    async def _send_loop(self, sess) -> None:
        while True:
            while self.queue:
                pcm = self.queue.popleft()
                try:
                    await sess.send_realtime_input(audio=types.Blob(data=pcm, mime_type=PCM_MIME))
                except Exception:
                    self.queue.appendleft(pcm)  # not delivered; retry after reconnect
                    raise
            if self.stopping:
                try:
                    await sess.send_realtime_input(audio_stream_end=True)
                except Exception:  # noqa: BLE001
                    pass
                return
            self._wake.clear()
            if not self.queue and not self.stopping:
                await self._wake.wait()

    async def _recv_loop(self, sess) -> None:
        while True:
            async for msg in sess.receive():
                sc = getattr(msg, "server_content", None)
                if sc is not None:
                    it = getattr(sc, "interim_input_transcription", None)
                    if it is not None and getattr(it, "text", None):
                        await self._emit(self.assembler.on_interim(it.text, getattr(it, "speaker_label", None), _parse_offset(getattr(it, "start_offset", None))))
                    tr = getattr(sc, "input_transcription", None)
                    if tr is not None and (getattr(tr, "text", None) or getattr(tr, "finished", None)):
                        await self._emit(self.assembler.on_input(tr.text or "", bool(tr.finished), getattr(tr, "speaker_label", None), _parse_offset(getattr(tr, "start_offset", None))))
                    if getattr(sc, "turn_complete", None):
                        await self._emit(self.assembler.on_turn_complete())
                if getattr(msg, "go_away", None):
                    raise ConnectionError("Gemini Live requested disconnect")
            await asyncio.sleep(0.01)

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(0.4)
            await self._emit(self.assembler.tick())


# --------------------------------------------------------------------------------------
# Uploaded audio transcription
# --------------------------------------------------------------------------------------
FILE_PROMPT = """Transcribe this hotel booking phone/desk conversation verbatim.
Language setting: {language}. The audio may mix Tamil and English - keep each word in the language spoken (Tamil in Tamil script, English in English). Do not translate.
Return ONLY JSON: {{"speech_detected": true|false, "segments": [{{"start": "MM:SS", "speaker": "Speaker 1", "role": "Hotel Booking Agent|Customer|Unknown", "text": "..."}}]}}
Rules: one segment per speaker turn; start is the time the turn begins; use consistent labels "Speaker 1", "Speaker 2" per voice; set role to {roles} ONLY if the content makes it clear, otherwise "Unknown" (never assume the first speaker is the agent); never invent speech; if there is no intelligible speech return speech_detected=false and an empty list."""

INLINE_LIMIT = 18 * 1024 * 1024


def parse_timestamp(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    parts = str(v or "0").strip().split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return 0.0
    sec = 0.0
    for n in nums:
        sec = sec * 60 + n
    return sec


def parse_file_transcript(text: str, roles: list) -> list:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.S).strip()
    data = json.loads(t)
    if isinstance(data, list):
        data = {"speech_detected": True, "segments": data}
    if not data.get("speech_detected", True):
        return []
    out = []
    role_lookup = {r.lower(): r for r in roles}
    for item in data.get("segments", []):
        txt = redact_cards(str(item.get("text", "")).strip())
        if not txt:
            continue
        speaker = str(item.get("speaker") or "Unknown").strip() or "Unknown"
        role = role_lookup.get(str(item.get("role", "")).strip().lower())
        out.append({"speaker_key": re.sub(r"\W+", "_", speaker.lower()).strip("_") or "unknown", "speaker_label": speaker,
                    "role": role, "text": txt, "start": parse_timestamp(item.get("start"))})
    for i, seg in enumerate(out):  # end = next start
        seg["end"] = out[i + 1]["start"] if i + 1 < len(out) else None
    return out


async def transcribe_file(gemini: GeminiClient, path, mime: str, language: str, roles: list) -> list:
    """Transcribe an audio file with Gemini. Raises GeminiServiceError on failure."""
    s = gemini.settings
    client = gemini.client
    size = path.stat().st_size
    uploaded = None
    try:
        if size <= INLINE_LIMIT:
            media = types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)
        else:
            uploaded = await client.aio.files.upload(file=str(path), config=types.UploadFileConfig(mime_type=mime))
            for _ in range(150):
                if getattr(getattr(uploaded, "state", None), "name", "ACTIVE") != "PROCESSING":
                    break
                await asyncio.sleep(2)
                uploaded = await client.aio.files.get(name=uploaded.name)
            media = types.Part.from_uri(file_uri=uploaded.uri, mime_type=mime)
        prompt = FILE_PROMPT.format(language=language, roles=" / ".join(f'"{r}"' for r in roles))
        cfg = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0, max_output_tokens=65536)
        last = "invalid response"
        for _ in range(2):
            try:
                resp = await client.aio.models.generate_content(model=s.upload_model, contents=[media, prompt], config=cfg)
            except Exception as e:  # noqa: BLE001
                err = gemini.classify(e)
                gemini.mark("transcription", False, err)
                raise err
            try:
                segs = parse_file_transcript(resp.text, roles)
                gemini.mark("transcription", True)
                return segs
            except (ValueError, json.JSONDecodeError, AttributeError) as e:
                last = type(e).__name__
        raise GeminiServiceError("invalid_response", f"Gemini returned an unreadable transcript ({last}). Please retry.")
    finally:
        if uploaded is not None:  # do not leave audio with the Files API
            try:
                await client.aio.files.delete(name=uploaded.name)
            except Exception:  # noqa: BLE001
                pass
