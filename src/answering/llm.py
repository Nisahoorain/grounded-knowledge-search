"""Free-tier answer backends.

Preferred architecture:
  Primary  = ANSWER_PROVIDER (default: gemini)
  Fallback = the other free provider on temporary 429 / RESOURCE_EXHAUSTED

Active providers: gemini | groq
Ollama helpers stay for the latency experiment write-up only.
"""
import json
import re
import time
import urllib.error
import urllib.request

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

ACTIVE_PROVIDERS = ("gemini", "groq")
_RETRY_SECONDS = re.compile(r"[Rr]etry in\s+([0-9]+(?:\.[0-9]+)?)s")


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


def _is_daily_quota(exc):
    """Daily free-tier exhaustion should fall back immediately, not sleep/retry."""
    msg = str(exc).lower()
    return (
        "per day" in msg
        or "requests per day" in msg
        or "free_tier_requests" in msg
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
    raise RuntimeError(f"Unknown provider={provider!r}. Use gemini or groq.")


def _call_with_backoff(provider, prompt, max_retries=None):
    """Same prompt/evidence; retry only on temporary rate limits."""
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
            # Daily quota will not clear with short sleeps — fail over now.
            if _is_daily_quota(exc) or not _is_rate_limited(exc) or attempt >= retries:
                return {
                    "provider": provider,
                    "text": None,
                    "error": str(exc),
                    "attempts": attempt + 1,
                }
            wait = _retry_after_seconds(exc, attempt)
            print(
                f"{provider} rate-limited; backoff {wait:.1f}s "
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


def chat_complete_with_meta(prompt):
    """Return answer text plus per-provider logs. Same prompt for primary and fallback."""
    provider = (ANSWER_PROVIDER or "gemini").strip().lower()
    if provider == "ollama":
        raise RuntimeError(
            "ANSWER_PROVIDER=ollama is disabled. "
            "Use gemini or groq. Ollama remains in code for the latency experiment only."
        )
    if provider not in ACTIVE_PROVIDERS:
        raise RuntimeError(
            f"Unknown ANSWER_PROVIDER={provider!r}. Use gemini or groq."
        )

    fallback = "groq" if provider == "gemini" else "gemini"
    attempts = []

    primary = _call_with_backoff(provider, prompt)
    attempts.append(primary)
    if primary["text"] is not None:
        return {
            "text": primary["text"],
            "provider_used": provider,
            "fallback_used": False,
            "attempts": attempts,
        }

    if not _is_rate_limited(RuntimeError(primary["error"] or "")):
        raise RuntimeError(
            f"{provider} answer generation failed: {primary['error']}"
        )

    print(f"{provider} exhausted/temporary failure; falling back to {fallback}", flush=True)
    secondary = _call_with_backoff(fallback, prompt)
    attempts.append(secondary)
    if secondary["text"] is not None:
        return {
            "text": secondary["text"],
            "provider_used": fallback,
            "fallback_used": True,
            "attempts": attempts,
        }

    raise RuntimeError(
        f"{provider} failed ({primary['error']}); "
        f"{fallback} fallback also failed: {secondary['error']}"
    )


def chat_complete(prompt):
    return chat_complete_with_meta(prompt)["text"]
