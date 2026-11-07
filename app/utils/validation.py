"""Validation helpers: ids, uploaded audio, text normalisation, card-number redaction."""
import re
import unicodedata

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")

# Formats supported by Gemini audio understanding.
AUDIO_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mp3",
    ".aiff": "audio/aiff",
    ".aif": "audio/aiff",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}
SUPPORTED_HELP = "Supported formats: WAV, MP3, AIFF, AAC, OGG, FLAC."


def is_safe_id(value: str) -> bool:
    return bool(value and SAFE_ID.match(value))


def validate_audio(filename: str, data: bytes, max_mb: float):
    """Return (mime, None) when valid, otherwise (None, error message)."""
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in (filename or "") else ""
    if ext not in AUDIO_TYPES:
        return None, f"Unsupported file type '{ext or 'unknown'}'. {SUPPORTED_HELP}"
    if not data:
        return None, "The uploaded file is empty."
    if len(data) > max_mb * 1024 * 1024:
        return None, f"File is larger than the {max_mb:g} MB limit."
    h = data[:12]
    ok = True
    if ext == ".wav":
        ok = h[:4] == b"RIFF" and h[8:12] == b"WAVE"
    elif ext in (".aiff", ".aif"):
        ok = h[:4] == b"FORM"
    elif ext == ".ogg":
        ok = h[:4] == b"OggS"
    elif ext == ".flac":
        ok = h[:4] == b"fLaC"
    elif ext in (".mp3", ".aac"):
        ok = h[:3] == b"ID3" or (len(h) > 1 and h[0] == 0xFF and (h[1] & 0xE0) == 0xE0)
    if not ok:
        return None, f"The file content does not look like a valid {ext[1:].upper()} file."
    return AUDIO_TYPES[ext], None


def normalize(text: str) -> str:
    """Lower-case, replace punctuation/symbols with spaces, collapse whitespace (Unicode safe)."""
    out = []
    for ch in unicodedata.normalize("NFKC", text or ""):
        cat = unicodedata.category(ch)
        out.append(" " if cat[0] in "PSZC" else ch.lower())
    return " ".join("".join(out).split())


def quote_supported(quote: str, source_text: str, threshold: float = 0.8) -> bool:
    """True when (almost) every token of the quote appears in the cited transcript text."""
    q = normalize(quote).split()
    if not q:
        return False
    src = normalize(source_text)
    if " ".join(q) in src:
        return True
    pool = set(src.split())
    hit = sum(1 for t in q if t in pool)
    return hit / len(q) >= threshold


_CARD = re.compile(r"(?<!\d)(?:\d[ \-]?){13,19}(?!\d)")


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for d in reversed(digits):
        n = int(d)
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return total % 10 == 0


def redact_cards(text: str) -> str:
    """Redact payment-card-like numbers so full card numbers are not stored."""

    def repl(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return "[card number redacted]" if 13 <= len(digits) <= 19 and _luhn(digits) else m.group(0)

    return _CARD.sub(repl, text or "")


def fmt_ts(seconds: float) -> str:
    s = max(0, int(seconds or 0))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"
