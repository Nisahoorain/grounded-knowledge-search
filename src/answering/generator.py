import re

from src.answering.calculation import review_supported_answer
from src.answering.llm import chat_complete_with_meta

# Supported questions on k=5 bottom out at 0.537 (fb_00591).
# Headcount, bitcoin, and stock price sit inside that band (0.62-0.73),
# so the gate only refuses a retrieval whose best dense score is clearly weaker.
MIN_DENSE_SCORE = 0.50

NUM_RE = re.compile(r"\d[\d,]*\.?\d*")
REF_GROUP_RE = re.compile(r"\[(E\d+(?:\s*,\s*E\d+)*)\]")
# Model sometimes apologises instead of INSUFFICIENT_EVIDENCE / a real answer.
_SOFT_REFUSAL_RE = re.compile(
    r"(?is)\b("
    r"i(?:'m| am) sorry\b.*\b(?:can'?t|cannot|unable)\b.*\bhelp\b"
    r"|i (?:can'?t|cannot) (?:help|assist|answer|provide)\b"
    r"|i(?:'m| am) (?:not )?unable to (?:help|assist|answer)\b"
    r"|unable to (?:help|assist|answer|provide)\b"
    r"|i (?:must|have to) refuse\b"
    r"|i won'?t (?:help|answer|provide)\b"
    r")\b"
)


def _norm_num(s):
    return s.replace(",", "").rstrip(".")


def _canon_num(s):
    text = _norm_num(str(s))
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _numbers(text):
    return {_norm_num(n) for n in NUM_RE.findall(text) if _norm_num(n)}


def _refs_in(text):
    refs = []
    for grp in REF_GROUP_RE.findall(text):
        refs += [r.strip() for r in grp.split(",")]
    return list(dict.fromkeys(refs))


def _normalize_refusal_text(text):
    """Map curly quotes/apostrophes so soft-refusal matching is reliable."""
    if not text:
        return ""
    return (
        str(text)
        .replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u00b4", "'")
        .replace("`", "'")
    )


def _is_soft_refusal(text):
    """True when the model declined instead of answering from evidence."""
    if not text:
        return False
    body = _normalize_refusal_text(text)
    body = REF_GROUP_RE.sub(" ", body)
    if "```calc" in body:
        body = body.split("```calc", 1)[0]
    return bool(_SOFT_REFUSAL_RE.search(body.strip()))


def _refusal_response(answer=None, llm=None):
    """Assignment-shaped refusal: no citations, not verified."""
    # Keep a clean refusal line without leaked [E#] tags.
    clean = _normalize_refusal_text(answer or "")
    clean = REF_GROUP_RE.sub("", clean).strip() or "I'm sorry, but I can't help with that."
    if _is_soft_refusal(clean):
        clean = "I'm sorry, but I can't help with that."
    out = {
        "status": "refused",
        "answer": clean,
        "citations": [],
        "verified": False,
    }
    if llm is not None:
        out["llm"] = llm
    return out


def low_retrieval_confidence(chunks):
    """Refuse only when dense is weak AND no BM25 support is visible."""
    dense = [c.get("dense_score") for c in chunks if c.get("dense_score") is not None]
    dense_ok = bool(dense) and max(dense) >= MIN_DENSE_SCORE
    bm25_ok = any(c.get("bm25_rank") is not None for c in chunks)
    # Statement-row attachments often lack dense_score; treat them as usable evidence.
    statement_ok = any(
        (c.get("content_type") or "").startswith("table")
        or c.get("statement")
        or str(c.get("chunk_id") or "").endswith(":stmt")
        or "statement" in str(c.get("chunk_id") or "").lower()
        for c in chunks
    )
    return not (dense_ok or bm25_ok or statement_ok)


def verify_claims(answer, citation_map, chunk_text, derived_numbers=None, extra_refs=None):
    """Har sentence ke numbers us sentence ke cited chunks mein dhoondta hai.

    derived_numbers sirf tab aate hain jab calculation block independently
    verify ho chuka ho. Us ke baghair purana missing-number check same rehta hai.
    """
    derived = derived_numbers or set()
    claims = []
    sentences = [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\[])|\n+", answer) if s.strip()]
    all_refs = _refs_in(answer)
    if extra_refs:
        all_refs = list(dict.fromkeys([*all_refs, *extra_refs]))
    all_refs = [r for r in all_refs if r in citation_map]

    for s in sentences:
        refs = [r for r in _refs_in(s) if r in citation_map] or all_refs
        body = REF_GROUP_RE.sub(" ", s)
        nums = {n for n in _numbers(body) if len(n) > 1 or "." in n}
        source_nums = set()
        for r in refs:
            source_nums |= _numbers(chunk_text[r])
        missing = sorted(
            n for n in nums
            if n not in source_nums and _canon_num(n) not in derived
        )
        claims.append({
            "sentence": s.strip(),
            "cited": refs,
            "uncited": not _refs_in(s),
            "missing_numbers": missing,
            "supported": bool(refs) and not missing,
        })
    return claims


def _evidence_priority(chunk):
    """Prefer named statement / table rows when packing the prompt."""
    text = (chunk.get("text") or "").lower()
    statement = (chunk.get("statement") or "").lower()
    content = (chunk.get("content_type") or "").lower()
    score = 0
    if statement:
        score += 3
    if content.startswith("table") or "|" in (chunk.get("text") or ""):
        score += 2
    if any(k in text or k in statement for k in (
        "balance sheet", "cash flow", "income", "operations", "revenue",
        "accounts payable", "inventory", "capital expenditure",
    )):
        score += 1
    # Keep original hybrid order as a weak tie-breaker via negative rank proxy.
    score += float(chunk.get("score") or 0) * 0.01
    return score


def _order_evidence(chunks):
    indexed = list(enumerate(chunks))
    indexed.sort(key=lambda pair: (-_evidence_priority(pair[1]), pair[0]))
    return [chunk for _, chunk in indexed]


def _format_evidence_block(chunks):
    evidence = []
    citation_map = {}
    chunk_text = {}
    for i, item in enumerate(chunks, start=1):
        ref = f"E{i}"
        citation_map[ref] = {
            "doc_id": item["doc_id"],
            "page": item["page"],
            "chunk_id": item["chunk_id"],
        }
        chunk_text[ref] = item["text"]
        meta_bits = [
            f"Document: {item['doc_id']}",
            f"Page: {item['page']}",
        ]
        if item.get("statement"):
            meta_bits.append(f"Statement: {item['statement']}")
        if item.get("section"):
            meta_bits.append(f"Section: {item['section']}")
        if item.get("content_type"):
            meta_bits.append(f"Type: {item['content_type']}")
        # Mechanical tag: model must copy [E#] verbatim, not invent doc/page cites.
        evidence.append(
            f"### [{ref}]\n"
            f"Cite exactly as [{ref}].\n"
            + ", ".join(meta_bits)
            + f"\n{item['text']}"
        )
    return evidence, citation_map, chunk_text


def _build_prompt(question, evidence_blocks):
    return f"""
You are a financial filing question-answering system.

Answer the question using ONLY the evidence below.

Rules:
1. Never use outside knowledge.
2. Do not invent facts or figures.
3. Every numeric claim or factual statement MUST be followed by its exact evidence tag [E#] from the list (example: [E1] or [E2, E3]). Vague doc/page mentions are not citations.
4. Prefer the evidence block whose statement/section heading best matches the metric the question asks for (for example revenue vs accounts receivable, or margin vs raw amount).
5. Evidence blocks ARE provided below. You are strictly forbidden from refusing for lack of information and must NOT reply INSUFFICIENT_EVIDENCE. Answer from the closest matching evidence and cite it with [E#].
6. Preserve units and reporting periods.
7. Be concise and finish the answer. Do not stop mid-sentence.
8. If the answer is copied from the evidence, stop. Do not add a calculation block, and do not average it with another year. If the statement says "in thousands" and the question asks for millions, divide by 1000 and round to the nearest million. Never repeat the thousands figure and call it millions.
9. If a figure is not printed in the evidence and you compute it (an average, change, ratio, margin, days outstanding, or any other formula), show the work after the answer in one block:

```calc
formula: <expression using names>
input: <name> = <number copied from that source> [E1]
input: <name> = <number taken from the question> [Q]
step: <name> = <expression> = <computed number>
final: <rounded result>
```

One input line per evidence number. An average or a change is a step, not an input: write the two amounts it comes from as inputs, then the average or change as a step. [Q] is only for a number the question itself states, such as 365 in a definition. Use only + - * / and parentheses.
This holds for every company. Compute the formula the question defines from the statement lines it names. A ratio printed somewhere else in the filing is a different figure.

Evidence:
{chr(10).join(evidence_blocks)}

Question: {question}

Answer:
"""


def _cite_retry_prompt(question, evidence_blocks, draft):
    return f"""
You previously drafted an answer without valid evidence IDs.

Rewrite the answer using ONLY the evidence below.
Every numeric claim or factual sentence MUST include its exact tag like [E1].
Evidence is provided. Do NOT reply INSUFFICIENT_EVIDENCE and do not refuse for lack of information.
Do not invent numbers. Prefer the evidence whose statement/section matches the asked metric.

Evidence:
{chr(10).join(evidence_blocks)}

Question: {question}

Draft answer to fix:
{draft}

Corrected answer:
"""


def _force_answer_prompt(question, evidence_blocks, draft):
    return f"""
You previously replied that evidence was insufficient, but evidence blocks WERE provided.

You must answer the question using ONLY the evidence below.
Every numeric claim or factual statement MUST be followed by its exact evidence tag [E#].
Do NOT reply INSUFFICIENT_EVIDENCE. Do not refuse.

Evidence:
{chr(10).join(evidence_blocks)}

Question: {question}

Previous draft (may be a refusal — replace it with a grounded answer):
{draft}

Answer:
"""


def _split_answer_and_calc(answer):
    text = answer or ""
    marker = "```calc"
    if marker in text:
        body, rest = text.split(marker, 1)
        return body.rstrip(), marker + rest
    return text.rstrip(), ""


def _auto_attach_citations(answer, citation_map, chunk_text):
    """If the model answered but forgot [E#], attach matching / top evidence tags."""
    if not answer or not citation_map:
        return answer, []

    used = [r for r in _refs_in(answer) if r in citation_map]
    if used:
        return answer, used

    body, calc = _split_answer_and_calc(answer)
    upper = body.upper()
    if "INSUFFICIENT_EVIDENCE" in upper:
        return answer, []

    # Skip empty / pure refusal shells.
    stripped = body.strip()
    if not stripped or stripped.lower().startswith("the provided filings do not"):
        return answer, []
    if stripped.lower().startswith("the system could not produce"):
        return answer, []

    answer_nums = {
        _canon_num(n)
        for n in _numbers(body)
        if len(_norm_num(n)) > 1 or "." in _norm_num(n)
    }
    matched = []
    for ref, text in chunk_text.items():
        chunk_nums = {_canon_num(n) for n in _numbers(text)}
        if answer_nums and (answer_nums & chunk_nums):
            matched.append(ref)

    if not matched:
        # Safety net: top packed evidence (already priority-ordered as E1..).
        matched = [next(iter(citation_map))]

    tag = "[" + ", ".join(matched) + "]"
    if not body.endswith(tag):
        body = f"{body} {tag}".rstrip()
    repaired = body if not calc else f"{body}\n{calc}"
    return repaired, matched


def _merge_llm_meta(first, second=None):
    if not second:
        return first
    return {
        "provider_used": second.get("provider_used") or first.get("provider_used"),
        "fallback_used": bool(first.get("fallback_used") or second.get("fallback_used")),
        "attempts": list(first.get("attempts") or []) + list(second.get("attempts") or []),
        "cite_retry": True,
    }


def generate_answer(question, retrieved_chunks):
    from src.answering.formula_eval import answer_from_formula, answer_from_reported_line

    direct = answer_from_formula(question, retrieved_chunks or [])
    if direct:
        return direct
    reported = answer_from_reported_line(question, retrieved_chunks or [])
    if reported:
        return reported

    if not retrieved_chunks or low_retrieval_confidence(retrieved_chunks):
        return {
            "status": "refused",
            "answer": "The provided filings do not contain sufficient evidence to answer this question.",
            "citations": [],
        }

    ordered = _order_evidence(retrieved_chunks)
    evidence_blocks, citation_map, chunk_text = _format_evidence_block(ordered)
    prompt = _build_prompt(question, evidence_blocks)

    meta = chat_complete_with_meta(prompt)
    answer = meta.get("text")
    # Provider chain (Gemini→Groq→Ollama) already tried. This is NOT an
    # evidence refusal — backends were unavailable.
    if not answer:
        return {
            "status": "provider_unavailable",
            "answer": None,
            "citations": [],
            "llm": meta,
            "error": meta.get("error") or "all answer backends failed",
        }

    # Soft policy / safety refusal from the model (must not become status=answered).
    if _is_soft_refusal(answer):
        return _refusal_response(llm=meta)

    # Evidence was in the prompt: do not hard-refuse on a first INSUFFICIENT.
    if "INSUFFICIENT_EVIDENCE" in (answer or "").upper():
        force_meta = chat_complete_with_meta(
            _force_answer_prompt(question, evidence_blocks, answer)
        )
        meta = _merge_llm_meta(meta, force_meta)
        meta["force_answer_retry"] = True
        answer = force_meta.get("text") or answer
        if _is_soft_refusal(answer):
            return _refusal_response(llm=meta)

    used_refs = _refs_in(answer or "")
    valid_refs = [r for r in used_refs if r in citation_map]

    # Citation repair: draft often has the right number but forgets [E#].
    # Do not cite-retry soft refusals into a fake answered citation.
    if (
        not valid_refs
        and "INSUFFICIENT_EVIDENCE" not in (answer or "").upper()
        and not _is_soft_refusal(answer)
    ):
        retry_meta = chat_complete_with_meta(
            _cite_retry_prompt(question, evidence_blocks, answer)
        )
        meta = _merge_llm_meta(meta, retry_meta)
        if retry_meta.get("text"):
            answer = retry_meta["text"]
        if _is_soft_refusal(answer):
            return _refusal_response(llm=meta)
        used_refs = _refs_in(answer or "")
        valid_refs = [r for r in used_refs if r in citation_map]

    # Safety net: keep a grounded answer and attach evidence tags mechanically.
    # Never auto-cite onto a soft refusal.
    cite_injected = False
    if not valid_refs and not _is_soft_refusal(answer):
        answer, valid_refs = _auto_attach_citations(answer, citation_map, chunk_text)
        cite_injected = bool(valid_refs)
        if cite_injected:
            meta = dict(meta)
            meta["cite_injected"] = True

    if (
        _is_soft_refusal(answer)
        or not valid_refs
        or "INSUFFICIENT_EVIDENCE" in (answer or "").upper()
    ):
        if _is_soft_refusal(answer):
            return _refusal_response(llm=meta)
        return _refusal_response(
            answer="The system could not produce an answer with verifiable citations.",
            llm=meta,
        )

    reviewed = review_supported_answer(answer, question, citation_map, chunk_text)
    response = {
        "status": reviewed["status"],
        "answer": answer,
        "citations": [citation_map[r] for r in valid_refs],
        "verification": reviewed["verification"],
        "unsupported_claims": reviewed["unsupported_claims"],
        "llm": meta,
    }
    if cite_injected:
        response["cite_injected"] = True
    if "calculation" in reviewed:
        response["calculation"] = reviewed["calculation"]
    return response
