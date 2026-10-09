# Bulk Certificate Generator

A FastAPI backend that accepts a list of recipients, generates a PDF certificate for each
one from a predefined template, tracks progress, and lets you download the results.

**Stack:** Python 3.12 · FastAPI · SQLAlchemy 2 + SQLite · ReportLab (PDF) · pytest · Docker

---

## 1. Setup & run (Docker)

Requires Docker with Compose v2.

```bash
docker compose up --build
```
## Manual testing in the browser (Swagger UI)

FastAPI ships with interactive API docs, so you can try every endpoint without writing any code.

1. Start the app:
```bash
   docker compose up --build
```
2. Open http://localhost:8000/docs
3. Expand `POST /jobs`, click **Try it out**, paste the request below, and click **Execute**:
```json
   {
     "event_name": "Python Bootcamp 2026",
     "issued_by": "Acme Academy",
     "recipients": [
       {"name": "Shiva", "email": "shiva@gmail.com"},
       {"name": "Raghu", "email": "Raghu@gmail.com"},
       {"name": "", "email": "broken"}
     ]
   }
```
   The response is `202 Accepted`. The third recipient is invalid, so it is marked as failed straight away.
4. Copy the `id` from the response.
5. Use `GET /jobs/{job_id}` to check status and progress. Once the worker has finished, the status should be `completed_with_errors` with 2 succeeded and 1 failed.
6. Use `GET /jobs/{job_id}/certificates?status=failed` to see the error for the invalid row (for example, `Name is required`).
7. Use `GET /jobs/{job_id}/certificates` to list all results. Open any `download_url` in the browser to view that recipient's PDF.
8. Open `http://localhost:8000/jobs/{job_id}/download` in the browser to download a ZIP of all successfully generated certificates.


- Interactive docs (try every endpoint in the browser): http://localhost:8000/docs
- Data (SQLite DB + generated PDFs) lives in the `certificate_data` Docker volume, so it
  survives restarts. `docker compose down -v` wipes it.



### Without Docker (optional)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q                                  # tests
uvicorn app.main:app --reload              # run the API (data goes to ./data)
```

## 3. Submit a certificate generation request

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "event_name": "Python Bootcamp 2026",
    "issued_by": "Acme Academy",
    "issue_date": "2026-10-01",
    "recipients": [
      {"name": "shiva",   "email": "shiva@gmail.com"},
      {"name": "Raghu", "email": "Raghu@gmail.com"},
      {"name": "",           "email": "broken-row"}
    ]
  }'
```

The response is `202 Accepted` and comes back immediately:

```json
{
  "id": "6f0c...-uuid",
  "status": "pending",
  "total": 3, "succeeded": 0, "failed": 1, "processed": 1,
  "progress_percent": 33.3,
  "certificates_url": "http://localhost:8000/jobs/6f0c.../certificates",
  "download_all_url": "http://localhost:8000/jobs/6f0c.../download",
  "...": "..."
}
```

`issue_date` is optional (defaults to today). Up to 1000 recipients per request
(`MAX_RECIPIENTS`).

## 4. Check progress

```bash
curl http://localhost:8000/jobs/<job_id>
```

| `status` | Meaning |
|---|---|
| `pending` | accepted, worker hasn't started |
| `processing` | worker is generating; see `progress_percent` |
| `completed` | every certificate succeeded |
| `completed_with_errors` | some succeeded, some failed |
| `failed` | nothing succeeded |

Poll until the status is one of the last three.

## 5. Retrieve the certificates

```bash
# Per-recipient results (filter with ?status=success|failed|pending, paginate with limit/offset)
curl "http://localhost:8000/jobs/<job_id>/certificates"
curl "http://localhost:8000/jobs/<job_id>/certificates?status=failed"   # what went wrong, and why

# One PDF  (use the download_url from the list above)
curl -o cert.pdf http://localhost:8000/jobs/<job_id>/certificates/<cert_id>/download

# Everything that succeeded, as one ZIP
curl -o certificates.zip http://localhost:8000/jobs/<job_id>/download
```

Each item in the list has `position` (index in your original request), `status`, `error`
(for failures) and `download_url` (for successes).

### API summary

| Method & path | Purpose |
|---|---|
| `POST /jobs` | Create a bulk job → `202` |
| `GET /jobs/{id}` | Status and progress |
| `GET /jobs/{id}/certificates` | Per-recipient results (filter, paginate) |
| `GET /jobs/{id}/certificates/{cert_id}/download` | One PDF |
| `GET /jobs/{id}/download` | ZIP of all successful PDFs (`409` while the job is still running) |
| `GET /health` | Liveness check |

---

## 6. Design decisions

**Background processing, not synchronous.** A request can contain up to 1000 recipients;
generating them inline would keep the HTTP request open for seconds to minutes and risk
client/proxy timeouts. `POST /jobs` instead validates, stores the job, returns `202`
immediately, and a FastAPI background task generates the PDFs. The client polls
`GET /jobs/{id}`. (Measured locally: 500 certificates ≈ 6 s total, with the POST returning
in ~0.15 s.) I chose `BackgroundTasks` over Celery/Redis to keep the project to a single
container with no extra infrastructure; see *Limitations* for what changes in production.

**Two levels of validation.**
- *Request level* (missing/empty `recipients`, blank `event_name`, malformed date, too many
  recipients) → the whole request is rejected with `422`, because nothing sensible can be done.
- *Row level* (blank name, invalid email, over-long name, duplicate email in the request) →
  that row is saved as `failed` with a human-readable `error`, and **all other rows still
  generate**. Invalid rows are recorded immediately at submit time, so the client sees them
  in the very first response.

**Failure isolation.** Each certificate is generated inside its own `try/except` in
`services/processor.py`, and the result is committed per certificate. One bad render can't
abort the job, progress is visible live, and a crash loses at most one item. Final job
status is derived from the counts (`completed` / `completed_with_errors` / `failed`).

**Resumable.** The worker only picks up `pending` certificates, so it is safe to re-run.
On startup the app resumes any job left in `pending`/`processing` by a restart.

**Safe file handling.** PDFs are written to `<STORAGE_DIR>/<job_id>/<certificate_id>.pdf`
using UUIDs, never recipient-supplied text, which rules out path traversal (`../../x`) and
filename collisions. PDFs are written to a temp file and renamed, so a failed render never
leaves a half-written file. Download filenames are sanitized.

**Template.** A single landscape-A4 template is drawn in code with ReportLab (pure Python,
no system dependencies beyond fonts). Long names/event titles auto-shrink to fit inside the
border. DejaVu fonts are installed in the image so accented/non-Latin names render correctly.
Each certificate prints its unique ID.

**Database.** SQLite via SQLAlchemy 2: relational, zero setup, WAL mode enabled so status
polling works while the worker writes. Two tables, `jobs` and `certificates` (FK +
index on `(job_id, status)`). Changing to Postgres only requires setting `DATABASE_URL`
(and adding a driver such as `psycopg`).

**Single uvicorn worker.** Background tasks run inside the API process and SQLite has one
writer, so the image runs one worker. Throughput on one worker is plenty for this workload.

## 7. Limitations / what I'd do next

- **Durability:** `BackgroundTasks` live in the API process. Restart recovery is covered by
  the resume-on-startup logic, but for real scale I'd move to a queue (Celery/RQ + Redis) with
  separate worker containers, and Postgres instead of SQLite.
- **Auth, rate limiting and per-user job ownership** are not implemented (out of scope).
- **Storage** is local disk (a Docker volume); production would use S3-style object storage
  and signed URLs. There is no cleanup/retention policy for old jobs.
- **Email validation** is a pragmatic regex (format only; no deliverability check).
- **Layout** is a single fixed template, as the brief specifies.

## 8. Configuration

| Env var | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:////app/data/certificates.db` (Docker) | SQLAlchemy URL |
| `STORAGE_DIR` | `/app/data/certificates` (Docker) | Where PDFs are written |
| `MAX_RECIPIENTS` | `1000` | Max recipients per request |

## 9. Project structure

```
app/
  main.py               # routes, startup/resume
  config.py             # env-based settings
  database.py           # engine + sessions (SQLite WAL)
  models.py             # Job, Certificate
  schemas.py            # request/response models
  services/
    jobs.py             # row validation + job creation
    processor.py        # background worker (failure isolation, progress)
    generator.py        # PDF template (ReportLab)
Dockerfile              # multi-stage: base / test / runtime
docker-compose.yml
```
