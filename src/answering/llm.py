"""Answer backends with a no-crash fallback chain.

Preferred architecture for final / submission runs:
  1. ANSWER_PROVIDER primary (default: gemini)
  2. The other free hosted provider (groq ↔ gemini)
  3. Local Ollama last resort

Failures (quota, 503, missing key, Ollama down) fall through the chain.
If every provider fails, return text=None instead of raising — callers refuse
with the assignment contract (status answered|refused), not a hard error.
"""
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from src.config import (
    ANSWER_PROVIDER,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GROQ_API_KEY,
    GROQ_MODEL,
    LLM_MAX_RETRIES,
    OLLAMA_MODEL,
    OLLAMA_URL,
)

ACTIVE_PROVIDERS = ("gemini", "groq", "ollama")
_RETRY_SECONDS = re.compile(r"[Rr]etry in\s+([0-9]+(?:\.[0-9]+)?)s")
_CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "llm_cache.json"
_cache = None


def _provider_chain(primary):
    """Gemini → Groq → Ollama (or Groq → Gemini → Ollama)."""
    name = (primary or "gemini").strip().lower()
    if name == "groq":
        return ["groq", "gemini", "ollama"]
    if name == "ollama":
        return ["ollama", "gemini", "groq"]
    return ["gemini", "groq", "ollama"]


def _load_cache():
    if not _CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _get_cache():
    global _cache
    if _cache is None:
        _cache = _load_cache()
    return _cache


def _save_cache():
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(_get_cache()), encoding="utf-8")


def _post_json(url, payload, headers, timeout=60):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc


def _is_rate_limited(exc):
    msg = str(exc)
    return (
        "HTTP 429" in msg
        or "RESOURCE_EXHAUSTED" in msg
        or "Rate limit" in msg
        or "rate_limit" in msg
    )


def _is_temporary(exc):
    """Retryable provider pressure: RPM 429, 503 high-demand, brief unavailability."""
    msg = str(exc)
    low = msg.lower()
    return (
        _is_rate_limited(exc)
        or "HTTP 503" in msg
        or "UNAVAILABLE" in msg
        or "high demand" in low
        or "temporarily" in low
        or "try again later" in low
    )


def _is_daily_quota(exc):
    """Daily free-tier exhaustion should fall back immediately, not sleep/retry."""
    msg = str(exc).lower()
    return (
        "per day" in msg
        or "requests per day" in msg
        or "free_tier_requests" in msg
        or "quota" in msg and ("per day" in msg or "daily" in msg)
        or "retry in" in msg and ("h" in msg or "hour" in msg)
    )


def _retry_after_seconds(exc, attempt):
    """Prefer short 'retry in Xs' hints; else exponential backoff capped at 60s."""
    msg = str(exc)
    match = _RETRY_SECONDS.search(msg)
    if match:
        return min(float(match.group(1)), 60.0)
    return min(2 ** attempt, 60)


def _chat_ollama(prompt):
    """Unused in production. Kept so the Ollama latency experiment stays reproducible."""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "keep_alive": "30m",
        "options": {"temperature": 0, "num_predict": 800},
    }
    result = _post_json(
        OLLAMA_URL,
        payload,
        {"Content-Type": "application/json"},
        timeout=120,
    )
    return result["message"]["content"].strip()


def _chat_groq(prompt):
    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY missing in .env "
            "(free key from https://console.groq.com/keys)"
        )
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 800,
    }
    result = _post_json(
        "https://api.groq.com/openai/v1/chat/completions",
        payload,
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )
    return result["choices"][0]["message"]["content"].strip()


def _chat_gemini(prompt):
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY missing in .env "
            "(free key from https://aistudio.google.com/apikey)"
        )
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 800,
        },
    }
    result = _post_json(url, payload, {"Content-Type": "application/json"})
    parts = result["candidates"][0]["content"]["parts"]
    return "".join(part.get("text", "") for part in parts).strip()


def _call_provider(provider, prompt):
    if provider == "groq":
        return _chat_groq(prompt)
    if provider == "gemini":
        return _chat_gemini(prompt)
    if provider == "ollama":
        return _chat_ollama(prompt)
    raise RuntimeError(f"Unknown provider={provider!r}. Use gemini, groq, or ollama.")


def _call_with_backoff(provider, prompt, max_retries=None):
    """Same prompt/evidence; retry temporary 429/503; fail over on daily quota."""
    retries = LLM_MAX_RETRIES if max_retries is None else max_retries
    last_exc = None
    for attempt in range(retries + 1):
        try:
            text = _call_provider(provider, prompt)
            return {
                "provider": provider,
                "text": text,
                "error": None,
                "attempts": attempt + 1,
            }
        except Exception as exc:
            last_exc = exc
            # Daily quota / hard errors: stop this provider and let the chain continue.
            if _is_daily_quota(exc) or not _is_temporary(exc) or attempt >= retries:
                return {
                    "provider": provider,
                    "text": None,
                    "error": str(exc),
                    "attempts": attempt + 1,
                }
            wait = _retry_after_seconds(exc, attempt)
            print(
                f"{provider} temporary failure; backoff {wait:.1f}s "
                f"(attempt {attempt + 1}/{retries + 1})",
                flush=True,
            )
            time.sleep(wait)
    return {
        "provider": provider,
        "text": None,
        "error": str(last_exc),
        "attempts": retries + 1,
    }


def _chat_complete_with_meta_uncached(prompt):
    """Try Gemini → Groq → Ollama (last resort).

    Hosted API failure must not look like an evidence refusal — we always
    continue to the next provider, ending on Ollama. Only if every backend
    fails do we return text=None (caller marks provider_unavailable).
    """
    primary = (ANSWER_PROVIDER or "gemini").strip().lower()
    if primary not in ACTIVE_PROVIDERS:
        primary = "gemini"

    chain = _provider_chain(primary)
    # Guarantee Ollama is last even if ANSWER_PROVIDER was odd.
    chain = [p for p in chain if p != "ollama"] + ["ollama"]
    attempts = []
    errors = []

    for i, name in enumerate(chain):
        if i > 0:
            print(
                f"{attempts[-1]['provider']} failed; falling back to {name}",
                flush=True,
            )
        # Give local Ollama more chances — it is the last option, not a refuse.
        retries = LLM_MAX_RETRIES + 2 if name == "ollama" else None
        result = _call_with_backoff(name, prompt, max_retries=retries)
        attempts.append(result)
        if result["text"] is not None:
            return {
                "text": result["text"],
                "provider_used": name,
                "fallback_used": i > 0,
                "attempts": attempts,
            }
        errors.append(f"{name}: {result.get('error')}")

    return {
        "text": None,
        "provider_used": None,
        "fallback_used": True,
        "attempts": attempts,
        "error": " | ".join(errors),
        "provider_unavailable": True,
    }


def chat_complete_with_meta(prompt):
    """Disk-cached wrapper. Temperature is 0, so identical prompts reuse answers."""
    key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    cache = _get_cache()
    hit = cache.get(key)
    if hit and hit.get("text"):
        return {**hit, "cached": True}

    out = _chat_complete_with_meta_uncached(prompt)
    # Cache only successful generations (never errors / empty).
    if out.get("text"):
        cache[key] = {
            "text": out["text"],
            "provider_used": out.get("provider_used"),
            "fallback_used": out.get("fallback_used"),
            "attempts": out.get("attempts"),
        }
        _save_cache()
    return out


def chat_complete(prompt):
    return chat_complete_with_meta(prompt)["text"]
