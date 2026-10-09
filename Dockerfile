# ---- base: system deps, python deps, app code --------------------------------
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# DejaVu fonts let certificates render names with accents / non-Latin scripts.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Run as a non-root user. /app/data holds the SQLite DB and generated PDFs.
RUN useradd --create-home appuser && mkdir -p /app/data && chown -R appuser /app
ENV DATABASE_URL=sqlite:////app/data/certificates.db \
    STORAGE_DIR=/app/data/certificates

COPY --chown=appuser:appuser app ./app

# ---- test: adds dev dependencies and the tests -------------------------------
FROM base AS test
USER root
COPY requirements-dev.txt pytest.ini ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY --chown=appuser:appuser tests ./tests
USER appuser
CMD ["pytest", "-q"]

# ---- runtime (default target) ------------------------------------------------
FROM base AS runtime
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
# One worker on purpose: SQLite allows a single writer, and background jobs run
# inside the API process. See "Design decisions" in the README.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
