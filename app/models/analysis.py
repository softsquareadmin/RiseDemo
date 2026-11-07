"""Schema for the structured JSON Gemini must return from conversation analysis."""
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

Status = Literal["MET", "NOT_MET", "NOT_APPLICABLE", "NEEDS_REVIEW"]
Priority = Literal["high", "medium", "low"]


class CriterionUpdate(BaseModel):
    topic_id: Optional[str] = None
    criterion_id: str
    status: Status
    evidence_segment_ids: list[str] = Field(default_factory=list)
    evidence_quote: str = ""
    reason: str = ""

    @field_validator("status", mode="before")
    @classmethod
    def _up(cls, v):
        return str(v).strip().upper().replace(" ", "_")


class BookingUpdate(BaseModel):
    field: str
    value: str
    confirmed: bool = False
    source_segment_id: str = ""

    @field_validator("value", mode="before")
    @classmethod
    def _str(cls, v):
        return "" if v is None else str(v)


class SuggestedQuestion(BaseModel):
    criterion_id: str
    question: str
    priority: Priority = "medium"

    @field_validator("priority", mode="before")
    @classmethod
    def _p(cls, v):
        v = str(v).lower().strip()
        return v if v in ("high", "medium", "low") else "medium"


class SpeakerHint(BaseModel):
    speaker_key: str
    role: str
    evidence_segment_ids: list[str] = Field(default_factory=list)


class ReportUpdate(BaseModel):
    summary: str = ""
    recommended_next_action: str = ""
    booking_status: str = ""


class FinalReport(BaseModel):
    outcome: str = ""
    missing_information: list[str] = Field(default_factory=list)
    questions_not_asked: list[str] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    session_id: str = ""
    evaluated_through_segment_id: str = ""
    criteria_updates: list[CriterionUpdate] = Field(default_factory=list)
    booking_updates: list[BookingUpdate] = Field(default_factory=list)
    suggested_questions: list[SuggestedQuestion] = Field(default_factory=list)
    speaker_hints: list[SpeakerHint] = Field(default_factory=list)
    report_update: ReportUpdate = Field(default_factory=ReportUpdate)
    final_report: Optional[FinalReport] = None

    @field_validator("booking_updates", mode="before")
    @classmethod
    def _booking(cls, v):
        # Accept the {"check_in_date": {"value": ..}} object form as well as a list.
        if isinstance(v, dict):
            out = []
            for k, item in v.items():
                if isinstance(item, dict):
                    out.append({"field": k, **item})
                else:
                    out.append({"field": k, "value": item})
            return out
        return v or []
