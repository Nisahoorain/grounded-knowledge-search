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


def retrieve_for_ask(question, role, k=5, rerank=False):
    return attach_statement_rows(
        question,
        role,
        search_hybrid(question, role, k=k, rerank=rerank),
    )


def to_contract(response):
    """Map internal statuses to the two the brief allows.

    answered_unverified -> status "answered" + verified False.
    Extra fields (verification, calculation, unsupported_claims) stay.
    """
    out = dict(response)
    status = out.get("status")
    if status == "answered_unverified":
        out["status"] = "answered"
        out["verified"] = False
    elif status == "answered":
        out.setdefault("verified", True)
    elif status != "refused":
        # Unknown internal status: safest public value is a refusal.
        out["status"] = "refused"
    return out