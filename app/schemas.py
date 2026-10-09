"""Pydantic schemas for requests and responses."""
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from app.config import settings


class RecipientIn(BaseModel):
    """Deliberately lenient. Content problems (blank name, bad email) are handled
    per row so one bad recipient does not reject the whole request."""

    name: str | None = None
    email: str | None = None


class JobCreate(BaseModel):
    event_name: str = Field(min_length=1, max_length=120, examples=["Python Bootcamp 2026"])
    issued_by: str = Field(min_length=1, max_length=100, examples=["Acme Academy"])
    issue_date: date = Field(default_factory=date.today)
    recipients: list[RecipientIn] = Field(min_length=1, max_length=settings.max_recipients)

    @field_validator("event_name", "issued_by")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class JobOut(BaseModel):
    id: str
    status: str
    event_name: str
    issued_by: str
    issue_date: date
    total: int
    succeeded: int
    failed: int
    processed: int
    progress_percent: float
    created_at: datetime
    updated_at: datetime
    certificates_url: str
    download_all_url: str


class CertificateOut(BaseModel):
    id: str
    position: int
    recipient_name: str | None
    recipient_email: str | None
    status: str
    error: str | None
    download_url: str | None


class CertificateList(BaseModel):
    job_id: str
    total: int  # number of items matching the filter
    limit: int
    offset: int
    items: list[CertificateOut]
