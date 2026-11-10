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

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:latest")

CHUNK_SIZE = _int("CHUNK_SIZE", 500)
CHUNK_OVERLAP = _int("CHUNK_OVERLAP", 100)


def _csv(name, default):
    parts = [part.strip() for part in os.getenv(name, "").split(",") if part.strip()]
    return parts or list(default)


# Role -> filing desks that role may retrieve. Lead sees every desk.
ROLE_DESKS = {
    "consumer": _csv("DESK_CONSUMER", ["consumer"]),
    "health_industrials": _csv("DESK_HEALTH_INDUSTRIALS", ["health_industrials"]),
    "tech_telecom": _csv("DESK_TECH_TELECOM", ["tech_telecom"]),
    "lead": _csv("DESK_LEAD", ["consumer", "health_industrials", "tech_telecom"]),
}
