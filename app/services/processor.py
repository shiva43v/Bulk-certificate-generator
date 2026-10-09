"""Background worker: turns pending certificates into PDFs.

Key properties:
  * each certificate is generated in its own try/except, so one failure never
    stops the rest of the job;
  * progress is committed after every certificate, so polling clients see live
    progress and a crash loses at most one item;
  * it only touches `pending` rows, so re-running it for a job is safe
    (used to resume unfinished jobs on startup).
"""
import logging

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.services import generator

logger = logging.getLogger(__name__)


def process_job(job_id: str) -> None:
    # The worker needs its own session: the request's session is closed by then.
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return

        job.status = JobStatus.PROCESSING.value
        db.commit()

        pending_ids = db.scalars(
            select(Certificate.id)
            .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.PENDING.value)
            .order_by(Certificate.position)
        ).all()

        for cert_id in pending_ids:
            cert = db.get(Certificate, cert_id)
            rel_path = f"{job_id}/{cert.id}.pdf"
            try:
                generator.generate_certificate_pdf(
                    settings.storage_dir / rel_path,
                    recipient_name=cert.recipient_name,
                    event_name=job.event_name,
                    issued_by=job.issued_by,
                    issue_date=job.issue_date,
                    certificate_id=cert.id,
                )
                cert.status = CertificateStatus.SUCCESS.value
                cert.file_path = rel_path
                job.success_count += 1
            except Exception as exc:  # noqa: BLE001 - isolate every per-item failure
                logger.exception("Certificate %s failed", cert.id)
                cert.status = CertificateStatus.FAILED.value
                cert.error = f"Generation failed: {exc}"[:500]
                job.failed_count += 1
            db.commit()

        if job.success_count == 0:
            job.status = JobStatus.FAILED.value
        elif job.failed_count > 0:
            job.status = JobStatus.COMPLETED_WITH_ERRORS.value
        else:
            job.status = JobStatus.COMPLETED.value
        db.commit()
