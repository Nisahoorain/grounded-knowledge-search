import re

from src.answering.calculation import review_supported_answer
from src.answering.llm import chat_complete_with_meta

# Supported questions on k=5 bottom out at 0.537 (fb_00591).
# Headcount, bitcoin, and stock price sit inside that band (0.62-0.73),
# so the gate only refuses a retrieval whose best dense score is clearly weaker.
MIN_DENSE_SCORE = 0.50

NUM_RE = re.compile(r"\d[\d,]*\.?\d*")
REF_GROUP_RE = re.compile(r"\[(E\d+(?:\s*,\s*E\d+)*)\]")


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
        evidence.append(f"[{ref}] " + ", ".join(meta_bits) + f"\n{item['text']}")
    return evidence, citation_map, chunk_text


def _build_prompt(question, evidence_blocks):
    return f"""
You are a financial filing question-answering system.

Answer the question using ONLY the evidence below.

Rules:
1. Never use outside knowledge.
2. Do not invent facts or figures.
3. Cite every factual claim using evidence IDs like [E1]. Every sentence that states a fact or number MUST include at least one [E#] from the evidence list.
4. Prefer the evidence block whose statement/section heading best matches the metric the question asks for (for example revenue vs accounts receivable, or margin vs raw amount).
5. If evidence is insufficient, reply exactly:
INSUFFICIENT_EVIDENCE
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
Every factual sentence MUST include at least one citation like [E1].
If the evidence cannot support the answer, reply exactly:
INSUFFICIENT_EVIDENCE

Do not invent numbers. Prefer the evidence whose statement/section matches the asked metric.

Evidence:
{chr(10).join(evidence_blocks)}

Question: {question}

Draft answer to fix:
{draft}

Corrected answer:
"""


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
    answer = meta["text"]

    if "INSUFFICIENT_EVIDENCE" in answer.upper():
        return {
            "status": "refused",
            "answer": "The provided filings do not contain sufficient evidence to answer this question.",
            "citations": [],
            "llm": meta,
        }

    used_refs = _refs_in(answer)
    valid_refs = [r for r in used_refs if r in citation_map]

    # Citation repair: draft often has the right number but forgets [E#].
    if not valid_refs:
        retry_meta = chat_complete_with_meta(
            _cite_retry_prompt(question, evidence_blocks, answer)
        )
        meta = _merge_llm_meta(meta, retry_meta)
        answer = retry_meta["text"]
        if "INSUFFICIENT_EVIDENCE" in answer.upper():
            return {
                "status": "refused",
                "answer": "The provided filings do not contain sufficient evidence to answer this question.",
                "citations": [],
                "llm": meta,
            }
        used_refs = _refs_in(answer)
        valid_refs = [r for r in used_refs if r in citation_map]

    if not valid_refs:
        return {
            "status": "refused",
            "answer": "The system could not produce an answer with verifiable citations.",
            "citations": [],
            "llm": meta,
        }

    reviewed = review_supported_answer(answer, question, citation_map, chunk_text)
    response = {
        "status": reviewed["status"],
        "answer": answer,
        "citations": [citation_map[r] for r in valid_refs],
        "verification": reviewed["verification"],
        "unsupported_claims": reviewed["unsupported_claims"],
        "llm": meta,
    }
    if "calculation" in reviewed:
        response["calculation"] = reviewed["calculation"]
    return response
