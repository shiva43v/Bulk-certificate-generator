"""FastAPI application: routes for creating jobs, checking progress, downloading certificates."""
import logging
import re
import tempfile
import threading
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.config import settings
from app.database import SessionLocal, engine, get_db
from app.models import Base, Certificate, CertificateStatus, Job, JobStatus
from app.schemas import CertificateList, CertificateOut, JobCreate, JobOut
from app.services.jobs import create_job
from app.services.processor import process_job

logger = logging.getLogger(__name__)

FINISHED = {JobStatus.COMPLETED.value, JobStatus.COMPLETED_WITH_ERRORS.value, JobStatus.FAILED.value}


def _resume_unfinished_jobs() -> None:
    """If the server restarted mid-job, finish the certificates still pending."""
    with SessionLocal() as db:
        ids = db.scalars(
            select(Job.id).where(Job.status.in_([JobStatus.PENDING.value, JobStatus.PROCESSING.value]))
        ).all()
    for job_id in ids:
        logger.info("Resuming unfinished job %s", job_id)
        threading.Thread(target=process_job, args=(job_id,), daemon=True).start()


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    _resume_unfinished_jobs()
    yield


app = FastAPI(
    title="Bulk Certificate Generator",
    description="Submit a list of recipients, track progress, and download generated PDF certificates.",
    version="1.0.0",
    lifespan=lifespan,
)


# ----------------------------------------------------------------- helpers
def _get_job_or_404(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _job_out(request: Request, job: Job) -> JobOut:
    processed = job.success_count + job.failed_count
    total = job.total_count
    return JobOut(
        id=job.id,
        status=job.status,
        event_name=job.event_name,
        issued_by=job.issued_by,
        issue_date=job.issue_date,
        total=total,
        succeeded=job.success_count,
        failed=job.failed_count,
        processed=processed,
        progress_percent=round(processed / total * 100, 1) if total else 100.0,
        created_at=job.created_at,
        updated_at=job.updated_at,
        certificates_url=str(request.url_for("list_certificates", job_id=job.id)),
        download_all_url=str(request.url_for("download_all", job_id=job.id)),
    )


def _safe_filename(name: str | None, suffix: str = "") -> str:
    base = re.sub(r"[^\w\-]+", "_", name or "certificate", flags=re.ASCII).strip("_")[:50] or "certificate"
    return f"{base}{suffix}.pdf"


# ------------------------------------------------------------------ routes
@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


@app.post("/jobs", response_model=JobOut, status_code=202, tags=["jobs"])
def submit_job(
    payload: JobCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Accept a bulk request. Returns immediately (202); generation runs in the background.

    Invalid recipients are marked failed right away and the rest are still generated.
    """
    job = create_job(db, payload)
    if job.status != JobStatus.FAILED.value:
        background_tasks.add_task(process_job, job.id)
    return _job_out(request, job)


@app.get("/jobs/{job_id}", response_model=JobOut, tags=["jobs"])
def get_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    """Job status and progress counts."""
    return _job_out(request, _get_job_or_404(db, job_id))


@app.get("/jobs/{job_id}/certificates", response_model=CertificateList, tags=["certificates"])
def list_certificates(
    job_id: str,
    request: Request,
    status: CertificateStatus | None = Query(None, description="Filter by certificate status"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Per-recipient results. Use `?status=failed` to see what went wrong."""
    _get_job_or_404(db, job_id)
    query = select(Certificate).where(Certificate.job_id == job_id)
    if status:
        query = query.where(Certificate.status == status.value)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Certificate.position).limit(limit).offset(offset)).all()
    items = [
        CertificateOut(
            id=c.id,
            position=c.position,
            recipient_name=c.recipient_name,
            recipient_email=c.recipient_email,
            status=c.status,
            error=c.error,
            download_url=str(request.url_for("download_certificate", job_id=job_id, cert_id=c.id))
            if c.status == CertificateStatus.SUCCESS.value
            else None,
        )
        for c in rows
    ]
    return CertificateList(job_id=job_id, total=total, limit=limit, offset=offset, items=items)


@app.get("/jobs/{job_id}/certificates/{cert_id}/download", tags=["certificates"])
def download_certificate(job_id: str, cert_id: str, db: Session = Depends(get_db)):
    """Download a single certificate PDF."""
    cert = db.get(Certificate, cert_id)
    if cert is None or cert.job_id != job_id:
        raise HTTPException(status_code=404, detail="Certificate not found")
    if cert.status != CertificateStatus.SUCCESS.value or not cert.file_path:
        raise HTTPException(status_code=409, detail=f"Certificate is not available (status: {cert.status})")
    path = settings.storage_dir / cert.file_path
    if not path.is_file():
        raise HTTPException(status_code=410, detail="Certificate file is missing from storage")
    return FileResponse(path, media_type="application/pdf", filename=_safe_filename(cert.recipient_name))


@app.get("/jobs/{job_id}/download", tags=["certificates"])
def download_all(job_id: str, db: Session = Depends(get_db)):
    """Download every successfully generated certificate in the job as a ZIP."""
    job = _get_job_or_404(db, job_id)
    if job.status not in FINISHED:
        raise HTTPException(status_code=409, detail=f"Job is still {job.status}; try again when it has finished")

    certs = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.SUCCESS.value)
        .order_by(Certificate.position)
    ).all()
    if not certs:
        raise HTTPException(status_code=404, detail="No certificates were generated for this job")

    # Build on disk (not in memory) so large jobs don't blow up RAM; deleted after sending.
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_STORED) as zf:  # PDFs are already compressed
        for c in certs:
            path = settings.storage_dir / c.file_path
            if path.is_file():
                zf.write(path, _safe_filename(c.recipient_name, suffix=f"_{c.id[:8]}"))
    return FileResponse(
        tmp.name,
        media_type="application/zip",
        filename=f"certificates_{job_id[:8]}.zip",
        background=BackgroundTask(lambda: Path(tmp.name).unlink(missing_ok=True)),
    )
