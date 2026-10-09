"""Application settings, read from environment variables."""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/certificates.db")
    storage_dir: Path = Path(os.getenv("STORAGE_DIR", "./data/certificates"))
    # Upper bound on recipients per request. Protects the server from huge payloads.
    max_recipients: int = int(os.getenv("MAX_RECIPIENTS", "1000"))


settings = Settings()
