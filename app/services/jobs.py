"""Job creation and per-recipient validation."""
import re

from sqlalchemy.orm import Session

from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.schemas import JobCreate

MAX_NAME_LEN = 100
MAX_EMAIL_LEN = 254
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_recipient(name: str | None, email: str | None) -> tuple[str | None, str | None, str | None]:
    """Return (clean_name, clean_email, error). `error` is None when the row is valid."""
    clean_name = (name or "").strip()
    clean_email = (email or "").strip().lower()

    if not clean_name:
        return None, None, "Name is required"
    if len(clean_name) > MAX_NAME_LEN:
        return None, None, f"Name must be at most {MAX_NAME_LEN} characters"
    # Control characters (tabs, newlines, etc.) don't belong in a certificate name.
    if any(ord(ch) < 32 for ch in clean_name):
        return None, None, "Name contains invalid control characters"
    if not clean_email:
        return None, None, "Email is required"
    if len(clean_email) > MAX_EMAIL_LEN or not _EMAIL_RE.match(clean_email):
        return None, None, "Email is not a valid email address"
    return clean_name, clean_email, None


def create_job(db: Session, payload: JobCreate) -> Job:
    """Persist a job and one Certificate row per recipient.

    Invalid rows (and duplicate emails within the job) are stored immediately as
    `failed` with a reason; valid rows are `pending` for the background worker.
    """
    job = Job(
        event_name=payload.event_name,
        issued_by=payload.issued_by,
        issue_date=payload.issue_date,
        total_count=len(payload.recipients),
    )
    seen_emails: set[str] = set()
    failed = 0

    for position, r in enumerate(payload.recipients):
        name, email, error = validate_recipient(r.name, r.email)
        if error is None and email in seen_emails:
            error = "Duplicate email in this request"
        if error is None:
            seen_emails.add(email)
            cert = Certificate(position=position, recipient_name=name, recipient_email=email)
        else:
            failed += 1
            cert = Certificate(
                position=position,
                recipient_name=r.name,
                recipient_email=r.email,
                status=CertificateStatus.FAILED.value,
                error=error,
            )
        job.certificates.append(cert)

    job.failed_count = failed
    if failed == job.total_count:
        job.status = JobStatus.FAILED.value  # nothing to process
    db.add(job)
    db.commit()
    return job
