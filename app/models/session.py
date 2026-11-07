"""Session state models (persisted as JSON)."""
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SessionStatus(str, Enum):
    CREATED = "CREATED"
    CONNECTING = "CONNECTING"
    RECORDING = "RECORDING"
    PROCESSING = "PROCESSING"
    FINALIZING = "FINALIZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Segment(BaseModel):
    id: str
    speaker_key: str = "unknown"  # raw label from the transcriber (e.g. spk_1)
    speaker: str = "Unknown"  # display label (role once reliably established)
    text: str
    start: float = 0.0
    end: Optional[float] = None


class Evidence(BaseModel):
    text: str = ""
    speaker: str = ""
    segment_id: str = ""
    segment_ids: list[str] = Field(default_factory=list)
    timestamp: str = ""


class CriterionState(BaseModel):
    status: str = "NOT_MET"
    evidence: Optional[Evidence] = None
    reason: str = ""
    revision: int = 0
    evaluated_at: Optional[str] = None


class BookingField(BaseModel):
    value: str
    confirmed: bool = False
    source_segment_id: str = ""
    revision: int = 0
    previous: list[str] = Field(default_factory=list)


class Session(BaseModel):
    id: str
    name: str
    language: str = "English"
    mode: str = "live"  # live | upload
    status: SessionStatus = SessionStatus.CREATED
    created_at: str = Field(default_factory=now_iso)
    duration_s: float = 0.0
    config: dict = Field(default_factory=dict)  # snapshot at session start
    consent: bool = False

    segments: list[Segment] = Field(default_factory=list)
    criteria: dict[str, CriterionState] = Field(default_factory=dict)
    booking: dict[str, BookingField] = Field(default_factory=dict)
    booking_status: str = "Unknown"
    summary: str = ""
    next_action: str = ""
    question_pool: dict[str, dict] = Field(default_factory=dict)

    evaluated_through: int = 0  # number of segments already analysed
    revision: int = 0  # last analysis revision issued
    applied_revision: int = 0  # last analysis revision merged
    analysis_count: int = 0
    finalized: bool = False
    final_report: dict = Field(default_factory=dict)

    speaker_map: dict[str, str] = Field(default_factory=dict)  # manual speaker corrections
    warnings: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    audio_file: Optional[str] = None
    audio_mime: Optional[str] = None
    timings: dict = Field(default_factory=dict)
    overall_display: str = "0%"  # cached for the Recent Sessions list

    def segment_by_id(self, sid: str) -> Optional[Segment]:
        for s in self.segments:
            if s.id == sid:
                return s
        return None
