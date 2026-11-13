from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)
    session_id: str | None = None

    @field_validator("session_id")
    @classmethod
    def _valid_session(cls, v: str | None) -> str | None:
        if v is not None and not SESSION_RE.match(v):
            raise ValueError("session_id must be 8-64 characters: letters, digits, '-' or '_'")
        return v


class SourceRef(BaseModel):
    url: str
    title: str
    page_type: str = ""


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceRef]
    session_id: str
    intent: str
    answered: bool
    language: str
    suggest_lead_capture: bool = False
    disclaimer_included: bool = False
    metadata: dict[str, Any] = {}


class LeadRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: str = Field(..., max_length=200)
    consent: bool
    session_id: str | None = None

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip()
        if not EMAIL_RE.match(v):
            raise ValueError("invalid email address")
        return v

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str) -> str:
        return re.sub(r"\s+", " ", v).strip()


class LeadResponse(BaseModel):
    saved: bool
    message: str
