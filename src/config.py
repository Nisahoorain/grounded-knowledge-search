import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]


def _load_env():
    env_path = PROJECT_DIR / ".env"
    if not env_path.exists():
        return

    with open(env_path, encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _int(name, default):
    return int(os.getenv(name, default))


_load_env()

# paths
PDF_DIR = PROJECT_DIR / "pdfs"
MANIFEST_PATH = PROJECT_DIR / "manifest.csv"
EXTRACTED_DIR = PROJECT_DIR / "data" / "extracted"
CHROMA_DIR = Path(os.getenv("CHROMA_DIR", PROJECT_DIR / "data" / "chroma_db"))

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

CHUNK_SIZE = _int("CHUNK_SIZE", 500)
CHUNK_OVERLAP = _int("CHUNK_OVERLAP", 100)
