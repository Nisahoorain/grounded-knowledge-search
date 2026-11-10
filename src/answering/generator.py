import json
import re
import urllib.request
import urllib.error

from src.answering.calculation import review_supported_answer
from src.config import OLLAMA_MODEL, OLLAMA_URL

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
    scores = [c.get("dense_score") for c in chunks if c.get("dense_score") is not None]
    return not scores or max(scores) < MIN_DENSE_SCORE


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

    evidence = []
    citation_map = {}
    chunk_text = {}

    for i, item in enumerate(retrieved_chunks, start=1):
        ref = f"E{i}"
        citation_map[ref] = {
            "doc_id": item["doc_id"],
            "page": item["page"],
            "chunk_id": item["chunk_id"],
        }
        chunk_text[ref] = item["text"]
        evidence.append(
            f"[{ref}] Document: {item['doc_id']}, "
            f"Page: {item['page']}\n{item['text']}"
        )

    prompt = f"""
You are a financial filing question-answering system.

Answer the question using ONLY the evidence below.

Rules:
1. Never use outside knowledge.
2. Do not invent facts or figures.
3. Cite every factual claim using evidence IDs like [E1].
4. If evidence is insufficient, reply exactly:
INSUFFICIENT_EVIDENCE
5. Preserve units and reporting periods.
6. Be concise.
7. If the answer is copied from the evidence, stop. Do not add a calculation block, and do not average it with another year. If the statement says "in thousands" and the question asks for millions, divide by 1000 and round to the nearest million. Never repeat the thousands figure and call it millions.
8. If a figure is not printed in the evidence and you compute it (an average, change, ratio, margin, days outstanding, or any other formula), show the work after the answer in one block:

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
{chr(10).join(evidence)}

Question: {question}

Answer:
"""

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "stream": False,
        "keep_alive": "30m",
        "options": {
            "temperature": 0,
            # The prose stays short. The calc block needs room for the formula,
            # each input, and each step; generation still stops early when it is done.
            "num_predict": 800,
        },
    }

    request = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))

        answer = result["message"]["content"].strip()

    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
        raise RuntimeError(f"Ollama answer generation failed: {exc}") from exc

    if "INSUFFICIENT_EVIDENCE" in answer.upper():
        return {
            "status": "refused",
            "answer": "The provided filings do not contain sufficient evidence to answer this question.",
            "citations": [],
        }

    used_refs = _refs_in(answer)
    valid_refs = [r for r in used_refs if r in citation_map]

    if not valid_refs:
        return {
            "status": "refused",
            "answer": "The system could not produce an answer with verifiable citations.",
            "citations": [],
        }

    reviewed = review_supported_answer(answer, question, citation_map, chunk_text)
    response = {
        "status": reviewed["status"],
        "answer": answer,
        "citations": [citation_map[r] for r in valid_refs],
        "verification": reviewed["verification"],
        "unsupported_claims": reviewed["unsupported_claims"],
    }
    if "calculation" in reviewed:
        response["calculation"] = reviewed["calculation"]
    return response
