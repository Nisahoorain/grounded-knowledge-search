"""Grounded answers. The model may use only the retrieved filings."""

import json
import re
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.config import MAX_CONTEXT_SIZE, TOP_K
from src.generation.llm import complete
from src.retrieval.hybrid_search import _requested_years, search, tokenize

_SYSTEM = """You answer financial questions from the numbered filings in the user message.
Use only those filings. Do not use outside knowledge.
A line such as "2021: (number)" means that parenthetical number is the reported amount, not zero.
Copy the amount for the year named in the question. Keep the unit printed in that filing.
If the filings do not contain the answer, say that the retrieved pages do not contain the answer.
When the answer is a calculation, name each input figure and the arithmetic.
Reply with one JSON object and no other text:
{"answer": "the printed amount and unit", "sources": [1], "grounded": true}
sources lists only the filing number you copied from. grounded is false when the filings do not contain the answer, and then sources is empty."""

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_YEAR_CELL = re.compile(r"^(?:19|20)\d{2}$")
_PAGE_MARK = re.compile(r"\s*\[Page:\s*\d+\]\s*")
_ZERO = re.compile(r"^0(?:\.0+)?$")
_ASKS_FIGURE = re.compile(r"\b(?:amount|how much|what is the|what was the|calculate)\b", re.I)


def _unit(text):
    lowered = (text or "").lower()
    if "in thousands" in lowered:
        return "thousand"
    if "in millions" in lowered:
        return "million"
    if "in billions" in lowered:
        return "billion"
    return ""


def _year_amounts(text):
    """Turn a statement row into year: amount lines. Other text is unchanged."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    header_at = None
    years = []
    for index, line in enumerate(lines):
        cells = [_PAGE_MARK.sub("", cell).strip() for cell in line.split("|")]
        found = [cell for cell in cells if _YEAR_CELL.match(cell)]
        if len(found) >= 2:
            header_at = index
            years = found
            break
    if header_at is None or header_at + 1 >= len(lines):
        return text, []
    values = [_PAGE_MARK.sub("", cell).strip() for cell in lines[header_at + 1].split("|")]
    if len(values) < 2:
        return text, []
    label = values[0]
    amounts = values[1 : 1 + len(years)]
    pairs = [(year, amount) for year, amount in zip(years, amounts) if amount]
    if len(pairs) < 2:
        return text, []
    prefix = lines[:header_at]
    body = [label] + [f"{year}: {amount}" for year, amount in pairs]
    return "\n".join(prefix + body), pairs


def _context(hits):
    """Pack every retrieved filing into the context budget.

    Each filing keeps its number, document, and page. Long text is shortened
    so a later filing is not dropped.
    """
    headers = []
    bodies = []
    for number, hit in enumerate(hits, start=1):
        headers.append(f"[{number}] doc_id={hit['doc_id']} page={hit['page']}\n")
        readable, _pairs = _year_amounts(hit.get("text") or "")
        bodies.append(readable.strip()[:600])
    if not headers:
        return "", []
    separators = 2 * (len(headers) - 1)
    remaining = max(MAX_CONTEXT_SIZE - sum(len(header) for header in headers) - separators, 0)
    parts = []
    for header, body in zip(headers, bodies):
        slots = len(headers) - len(parts)
        share = remaining // slots if slots else 0
        snippet = body[:share]
        remaining -= len(snippet)
        parts.append(header + snippet)
    return "\n\n".join(parts), list(hits)


def _object(raw):
    text = raw.strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("answer was not JSON")
    return json.loads(text[start : end + 1])


def _source_index(item, hits):
    if isinstance(item, dict):
        doc_id = str(item.get("doc_id") or "")
        try:
            page = int(item.get("page"))
        except (TypeError, ValueError):
            return None
        for index, hit in enumerate(hits):
            if hit["doc_id"] == doc_id and int(hit["page"]) == page:
                return index
        return None
    try:
        return int(item) - 1
    except (TypeError, ValueError):
        return None


def _sources(raw_sources, hits):
    chosen = []
    seen = set()
    for item in raw_sources or []:
        index = _source_index(item, hits)
        if index is None or index < 0 or index >= len(hits):
            continue
        hit = hits[index]
        key = (hit["doc_id"], int(hit["page"]))
        if key in seen:
            continue
        seen.add(key)
        chosen.append(
            {
                "doc_id": hit["doc_id"],
                "page": int(hit["page"]),
                "chunk_id": hit["chunk_id"],
            }
        )
    return chosen


def _public(reply, citations, answered):
    """Shape required by the ask command."""
    clean = []
    seen = set()
    if answered:
        for item in citations or []:
            key = (item["doc_id"], int(item["page"]), item["chunk_id"])
            if key in seen:
                continue
            seen.add(key)
            clean.append(
                {
                    "doc_id": item["doc_id"],
                    "page": int(item["page"]),
                    "chunk_id": item["chunk_id"],
                }
            )
    if not clean:
        answered = False
    text = (reply or "").strip() or "The retrieved pages do not contain the answer."
    if not answered:
        text = "The retrieved pages do not contain the answer."
        clean = []
    return {"status": "answered" if answered else "refused", "answer": text, "citations": clean}


def _scale(amount, unit, question):
    cleaned = amount.replace(",", "").replace("$", "").strip()
    bare = cleaned.strip("()")
    try:
        value = float(bare)
    except ValueError:
        return f"{amount} {unit}".strip()
    asks_billions = re.search(r"\bbillions?\b", question or "", re.I)
    asks_millions = re.search(r"\bmillions?\b", question or "", re.I)
    if unit == "thousand" and asks_millions:
        value /= 1000
        rendered = f"{value:.3f}".rstrip("0").rstrip(".")
        return f"{rendered} million (printed as {amount} thousand)"
    if unit == "thousand" and asks_billions:
        value /= 1000000
        rendered = f"{value:.3f}".rstrip("0").rstrip(".")
        return f"{rendered} billion (printed as {amount} thousand)"
    if unit == "million" and asks_billions:
        value /= 1000
        rendered = f"{value:.3f}".rstrip("0").rstrip(".")
        return f"{rendered} billion (printed as {amount} million)"
    if unit == "billion" and asks_millions:
        value *= 1000
        rendered = f"{value:.3f}".rstrip("0").rstrip(".")
        return f"{rendered} million (printed as {amount} billion)"
    return f"{amount} {unit}".strip()


def _best_cell(question, hits, year):
    """Pick the statement row whose label shares the most words with the question."""
    best = None
    for hit in hits:
        readable, pairs = _year_amounts(hit.get("text") or "")
        lines = readable.splitlines()
        label = ""
        for index, line in enumerate(lines):
            if _YEAR.search(line) and ":" in line:
                label = lines[index - 1] if index else ""
                break
        score = len(set(tokenize(label)) & set(tokenize(question)))
        if score < 1:
            continue
        for row_year, amount in pairs:
            if row_year != year or _ZERO.match(amount.strip("()$ ")):
                continue
            if best is None or score > best[0]:
                best = (score, amount, hit)
    return best


def _from_printed_row(question, hits):
    """Use the matching statement cell when the model answers 0 for a figure question."""
    if not _ASKS_FIGURE.search(question or ""):
        return None
    years = {str(year) for year in _requested_years(question)}
    if len(years) != 1:
        return None
    found = _best_cell(question, hits, next(iter(years)))
    if not found:
        return None
    _score, amount, hit = found
    return _public(
        _scale(amount, _unit(hit.get("text") or ""), question),
        [
            {
                "doc_id": hit["doc_id"],
                "page": int(hit["page"]),
                "chunk_id": hit["chunk_id"],
            }
        ],
        True,
    )


def _align_units(question, sources, hits):
    """Scale a printed statement cell when the question asks for another unit."""
    if not _ASKS_FIGURE.search(question or ""):
        return None
    years = {str(year) for year in _requested_years(question)}
    if len(years) != 1 or not sources:
        return None
    year = next(iter(years))
    cited = [
        hit
        for hit in hits
        if any(
            hit["doc_id"] == source["doc_id"]
            and int(hit["page"]) == int(source["page"])
            and hit["chunk_id"] == source["chunk_id"]
            for source in sources
        )
    ]
    found = _best_cell(question, cited, year)
    if not found:
        return None
    _score, amount, hit = found
    unit = _unit(hit.get("text") or "")
    scaled = _scale(amount, unit, question)
    if unit and scaled != f"{amount} {unit}".strip():
        return scaled
    return None


def answer(question, role, k=None):
    """Answer a question from the top filings for this role.

    The model sees only the hybrid-search chunks. ``citations`` are the
    retrieved chunk, document, and page the answer used. ``status`` is
    ``refused`` when those filings do not contain the answer.
    """
    text = (question or "").strip()
    if not text:
        raise ValueError("question is required")
    limit = TOP_K if k is None else int(k)
    if limit < 1:
        raise ValueError("k must be at least 1")

    hits = search(text, role, limit)
    # Single-year figure questions: answer directly from the printed row.
    printed = _from_printed_row(text, hits)
    if printed:
        return printed
    context, kept = _context(hits)
    if not context:
        return _public("", [], False)

    raw = complete(
        f"Question: {text}\n\nFilings:\n{context}",
        system=_SYSTEM,
    )
    try:
        parsed = _object(raw)
    except (ValueError, json.JSONDecodeError):
        parsed = {"answer": raw, "sources": [], "grounded": False}

    sources = _sources(parsed.get("sources"), kept)
    grounded = bool(parsed.get("grounded")) and bool(sources)
    reply = str(parsed.get("answer") or "").strip()
    if _ZERO.match(reply) or not reply:
        printed = _from_printed_row(text, kept)
        if printed:
            return printed
    if grounded:
        scaled = _align_units(text, sources, kept)
        if scaled:
            reply = scaled
        return _public(reply, sources, True)
    return _public("", [], False)


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "--role":
        print('Usage: python src/generation/answer.py --role <role> "question"')
        sys.exit(1)
    role = sys.argv[2]
    question = " ".join(sys.argv[3:]).strip()
    print(json.dumps(answer(question, role), indent=2))
