"""Conversation checklist configuration models (hotel-agnostic)."""
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")
LANGUAGES = ["English", "Tamil", "English-Tamil (Mixed)"]


def slugify(text: str, fallback: str = "item") -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return (s or fallback)[:60]


def _unique(base: str, used: set) -> str:
    cand, n = base, 2
    while cand in used:
        cand = f"{base}_{n}"
        n += 1
    used.add(cand)
    return cand


class Criterion(BaseModel):
    id: str = ""
    label: str
    instruction: str = ""
    description: str = ""
    question: str = ""  # optional default follow-up question
    priority: Literal["high", "medium", "low"] = "medium"

    @field_validator("label")
    @classmethod
    def _label(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Criterion label is required")
        return v[:200]


class Topic(BaseModel):
    id: str = ""
    name: str
    criteria: list[Criterion] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Topic name is required")
        return v[:200]


class Configuration(BaseModel):
    id: str = ""
    name: str
    description: str = ""
    roles: list[str] = Field(default_factory=lambda: ["Hotel Booking Agent", "Customer"])
    language: str = "English"
    topics: list[Topic] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Configuration name is required")
        return v[:200]

    @field_validator("language")
    @classmethod
    def _lang(cls, v: str) -> str:
        if v not in LANGUAGES:
            raise ValueError(f"Language must be one of {LANGUAGES}")
        return v

    @model_validator(mode="after")
    def _finalize(self) -> "Configuration":
        if not self.id:
            self.id = slugify(self.name, "config")
        if not ID_RE.match(self.id):
            raise ValueError("Configuration id may only contain a-z, 0-9, '_' and '-'")
        self.roles = [r.strip() for r in self.roles if r and r.strip()] or ["Hotel Booking Agent", "Customer"]
        if not self.topics:
            raise ValueError("A configuration needs at least one topic")
        topic_ids: set = set()
        crit_ids: set = set()  # criterion ids are globally unique -> no double counting
        for t in self.topics:
            if not t.criteria:
                raise ValueError(f"Topic '{t.name}' needs at least one criterion")
            t.id = _unique(slugify(t.id or t.name, "topic"), topic_ids)
            for c in t.criteria:
                c.id = _unique(slugify(c.id or c.label, "criterion"), crit_ids)
        return self
