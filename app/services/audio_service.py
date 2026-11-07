"""Audio helpers: raw PCM16 (16 kHz mono) captured live -> WAV file for optional storage/playback."""
import wave
from pathlib import Path

SAMPLE_RATE = 16000


def pcm_to_wav(pcm_path: Path, wav_path: Path) -> None:
    """Stream-convert a raw PCM16 mono 16 kHz file to WAV without loading it all into memory."""
    with open(pcm_path, "rb") as src, wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        while chunk := src.read(1 << 20):
            w.writeframes(chunk)
