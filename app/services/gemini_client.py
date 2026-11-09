"""Shared Google Gemini client + error classification. Gemini is the only AI provider."""
import asyncio
import logging

from google import genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors

from app.config import Settings

log = logging.getLogger("repassist.gemini")


class GeminiServiceError(Exception):
    """kind: missing_key | auth | quota | model | network | invalid_response | other"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.kind in ("network", "other")


MESSAGES = {
    "missing_key": "No Gemini API key found. Add GOOGLE_API_KEY (or GEMINI_API_KEY) to the .env file and restart the server.",
    "auth": "Gemini rejected the API key (authentication error). Check the key in .env and restart the server.",
    "quota": "Gemini quota or rate limit exceeded. The service is temporarily unavailable - try again shortly.",
    "model": "The configured Gemini model is unavailable or does not support this operation. Check the model settings in .env.",
    "network": "Network problem while contacting Gemini.",
}


def classify_error(exc: BaseException, api_key: str = "") -> GeminiServiceError:
    if isinstance(exc, GeminiServiceError):
        return exc
    text = str(exc)
    if api_key:
        text = text.replace(api_key, "***")  # never leak the key
    low = text.lower()
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    # Live (WebSocket) handshake rejected, e.g. websockets InvalidStatus: the HTTP status is on .response
    hs = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(hs, int):
        if hs in (401, 403):
            return GeminiServiceError("auth", MESSAGES["auth"])
        if hs in (400, 404):
            # Google answers 404 for an unknown/retired model and sometimes for a bad key (REST and Live alike).
            return GeminiServiceError("model", f"Gemini rejected the request (HTTP {hs}). Usually the model name is not available to your API key "
                                      "(check ANALYSIS_MODEL / LIVE_TRANSCRIPTION_MODEL) or the key is invalid.")
        if hs == 429:
            return GeminiServiceError("quota", MESSAGES["quota"])
        return GeminiServiceError("network", f"Gemini Live is temporarily unavailable (HTTP {hs}).")
    if isinstance(exc, genai_errors.APIError) or code is not None:
        if "api key" in low or "api_key" in low or code in (401, 403):
            return GeminiServiceError("auth", MESSAGES["auth"])
        if code == 429 or "quota" in low or "exhausted" in low or "rate limit" in low:
            return GeminiServiceError("quota", MESSAGES["quota"])
        if code == 404 or "not found" in low or "not supported" in low or "unsupported" in low:
            return GeminiServiceError("model", MESSAGES["model"] + f" ({text[:160]})")
        if isinstance(code, int) and code >= 500:
            return GeminiServiceError("network", "Gemini service error (temporary).")
        return GeminiServiceError("other", f"Gemini request failed: {text[:200]}")
    if "api key" in low:
        return GeminiServiceError("auth", MESSAGES["auth"])
    if "quota" in low or "exhausted" in low:
        return GeminiServiceError("quota", MESSAGES["quota"])
    if "not found" in low or "not supported" in low or "unsupported" in low or "invalid argument" in low:
        return GeminiServiceError("model", MESSAGES["model"] + f" ({text[:160]})")
    if isinstance(exc, (ConnectionError, OSError, asyncio.TimeoutError, TimeoutError)) or "websocket" in low or "connection" in low:
        return GeminiServiceError("network", MESSAGES["network"])
    return GeminiServiceError("other", f"Unexpected Gemini error: {text[:200]}")


class GeminiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None
        # Honest service status; "ai_ready" only means a key is configured.
        self.status = {"transcription": "not_tested", "analysis": "not_tested"}

    @property
    def client(self) -> genai.Client:
        if not self.settings.configured:
            raise GeminiServiceError("missing_key", MESSAGES["missing_key"])
        if self._client is None:
            # SDK 3.x defaults to API v1, which has no Live endpoint (HTTP 404) - pin v1beta.
            self._client = genai.Client(api_key=self.settings.api_key, http_options=genai_types.HttpOptions(api_version="v1beta"))
        return self._client

    def mark(self, which: str, ok: bool, err: GeminiServiceError | None = None) -> None:
        self.status[which] = "ok" if ok else f"error:{err.kind if err else 'other'}"

    def classify(self, exc: BaseException) -> GeminiServiceError:
        return classify_error(exc, self.settings.api_key)

    async def check_connectivity(self) -> dict:
        """Tiny real call to the analysis model (explicit user action only)."""
        from google.genai import types

        try:
            await self.client.aio.models.generate_content(
                model=self.settings.analysis_model,
                contents="Reply with the word OK.",
                config=types.GenerateContentConfig(max_output_tokens=8),
            )
            self.mark("analysis", True)
        except Exception as e:  # noqa: BLE001
            err = self.classify(e)
            self.mark("analysis", False, err)
            return {"ok": False, "kind": err.kind, "message": err.message}
        return {"ok": True}
