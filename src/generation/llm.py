"""Local LLM wrapper. The default provider is Ollama."""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.config import LLM_MODEL, LLM_PROVIDER

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


def complete(prompt, system=None):
    """Return the model text for one prompt.

    ``LLM_PROVIDER`` selects the backend. Only ``ollama`` is supported.
    """
    if LLM_PROVIDER != "ollama":
        raise ValueError(f"unsupported LLM provider: {LLM_PROVIDER}")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": LLM_MODEL,
        "stream": False,
        "keep_alive": "30m",
        "messages": messages,
        "options": {"temperature": 0, "num_predict": 80},
    }
    request = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Ollama is not available at {OLLAMA_HOST}. Start it and pull {LLM_MODEL}."
        ) from exc
    message = (body.get("message") or {}).get("content")
    if not message or not str(message).strip():
        raise RuntimeError("Ollama returned an empty answer")
    return str(message).strip()
