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

# models (independent of each other)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3.2")

# chunking / retrieval
CHUNK_SIZE = _int("CHUNK_SIZE", 1000)
CHUNK_OVERLAP = _int("CHUNK_OVERLAP", 150)
TOP_K = _int("TOP_K", 5)
MAX_CONTEXT_SIZE = _int("MAX_CONTEXT_SIZE", 3500)
