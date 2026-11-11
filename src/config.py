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

# Answer backend: gemini (primary default) or groq. One env change switches.
# On HTTP 429 the other free provider is tried with the same prompt.
# Ollama is not an active option; its helpers stay in llm.py for experiments.
ANSWER_PROVIDER = os.getenv("ANSWER_PROVIDER", "gemini").strip().lower()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

# Rate-limit controls for free-tier eval / ask.
LLM_REQUEST_DELAY_S = float(os.getenv("LLM_REQUEST_DELAY_S", "8"))
LLM_MAX_RETRIES = _int("LLM_MAX_RETRIES", 3)

# Kept for the Ollama latency experiment only (not selected by ANSWER_PROVIDER).
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
