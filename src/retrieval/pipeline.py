"""Shared ask-path helpers.

Put this file at src/retrieval/pipeline.py.

main.py `ask` and eval/evaluate_answers.py both call retrieve_for_ask(), so the
eval measures the same retrieval that `ask` really uses (including the
statement rows). to_contract() makes sure the JSON `status` is only
"answered" or "refused", as the brief requires.
"""
from src.retrieval.hybrid import search_hybrid
from src.retrieval.statement_rows import attach_statement_rows

ALLOWED_STATUS = {"answered", "refused"}


def retrieve_for_ask(question, role, k=10, rerank=False):
    return attach_statement_rows(
        question,
        role,
        search_hybrid(
            question,
            role,
            k=k,
            candidate_k=300,
            page_dedupe=3,
            rerank=rerank,
        ),
    )


def to_contract(response, public=False):
    """Map internal statuses for `ask`.

    answered_unverified -> status "answered" + verified False.
    refused = evidence/citation could not support an answer (NOT API failure).
    provider_unavailable = Gemini/Groq/Ollama all failed (kept distinct).

    public=True (CLI): only status/answer/citations/verified. Assignment allows
    answered|refused — provider_unavailable is not remapped to refused; CLI
    handles it by exiting non-zero instead of faking a refusal.
    """
    # Late safety net: model soft-refusals must never ship as answered+cited.
    from src.answering.generator import _is_soft_refusal, _refusal_response

    out = dict(response)
    if _is_soft_refusal(out.get("answer")):
        out = _refusal_response(answer=out.get("answer"), llm=out.get("llm"))

    status = out.get("status")
    if status == "answered_unverified":
        out["status"] = "answered"
        out["verified"] = False
    elif status == "answered":
        out.setdefault("verified", True)
    elif status == "refused":
        # Refusal is never "verified" and must not carry citations.
        out["verified"] = False
        out["citations"] = []
    elif status in {"provider_unavailable", "error"}:
        # Keep distinct from evidence refusal.
        out["status"] = "provider_unavailable"
        out["answer"] = None
        out.setdefault("citations", [])
        out["verified"] = None
    else:
        out["status"] = "refused"

    if public:
        return {
            "status": out.get("status"),
            "answer": out.get("answer"),
            "citations": out.get("citations") or [],
            "verified": out.get("verified"),
        }
    return out