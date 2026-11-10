"""Compute a formula the question defines, from the statement rows in hand.

The model is not asked to do this arithmetic. Each operand is read from the
year columns of the matching line, then the expression is evaluated here.
"""

import re
from decimal import Decimal, ROUND_HALF_UP

from src.answering.calculation import eval_arithmetic
from src.retrieval.statement_rows import (
    _candidate_lines,
    _label_rank,
    aliases_for_operand,
    choose_row,
    line_items_in,
    statements_in_question,
)

_DEFINED = re.compile(r"defined\s+as\s*:\s*(.+?)(?:\.\s|round\b|$)", re.I | re.S)
_AVERAGE = re.compile(
    r"average\s+(.+?)\s+between\s+(?:fy\s*)?((?:19|20)\d{2})\s+and\s+(?:fy\s*)?((?:19|20)\d{2})",
    re.I,
)
_CHANGE = re.compile(
    r"change\s+in\s+(.+?)\s+between\s+(?:fy\s*)?((?:19|20)\d{2})\s+and\s+(?:fy\s*)?((?:19|20)\d{2})",
    re.I,
)
_YEAR_LINE = re.compile(
    r"(?:fy\s*)?((?:19|20)\d{2})\s+([a-z][a-z0-9]*(?:\s+[a-z][a-z0-9]*){0,4})(?=\s*[\+\-\*/\)]|\s*$)",
    re.I,
)
_PLACES = re.compile(r"\b(one|two|three|four|\d+)\s+decimal", re.I)
_PLACE_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4}
_PAGE = re.compile(r"\[page:\s*\d+\]", re.I)


def _fmt(value):
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _round(value, places):
    quantum = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def _decimal_places(question):
    match = _PLACES.search(question or "")
    if not match:
        return None
    word = match.group(1).lower()
    if word in _PLACE_WORDS:
        return _PLACE_WORDS[word]
    return int(word)


def _ident(text):
    name = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if not name or name[0].isdigit():
        name = "n_" + name
    return name


def _column_years(text):
    for line in text.splitlines():
        if line.lower().strip().startswith("columns:"):
            years = [int(year) for year in re.findall(r"(?:19|20)\d{2}", line)]
            if len(years) >= 2:
                return years
    return []


def _amount_cells(line):
    values = []
    for cell in _PAGE.sub(" ", line).split("|"):
        cell = cell.strip()
        if not re.search(r"\d", cell):
            continue
        if re.search(r"[A-Za-z]", cell):
            continue
        numbers = re.findall(r"\d[\d,]*(?:\.\d+)?", cell)
        if len(numbers) != 1:
            continue
        value = float(numbers[0].replace(",", ""))
        if "(" in cell and ")" in cell:
            value = -value
        values.append(value)
    return values


def _series_from_chunk(chunk, aliases):
    years = _column_years(chunk.get("text") or "")
    best_rank = 0
    best_line = None
    for line in _candidate_lines(chunk.get("text") or ""):
        rank = _label_rank(line, aliases)
        if rank > best_rank:
            best_rank = rank
            best_line = line
    if best_rank < 3 or not best_line or len(years) < 2:
        return None
    amounts = _amount_cells(best_line)
    count = min(len(years), len(amounts))
    if count < 2:
        return None
    return dict(zip(years[:count], amounts[:count]))


def _series(operand, chunks):
    aliases = aliases_for_operand(operand)
    if not aliases:
        return None, None
    best = None
    best_rank = 0
    for chunk in chunks:
        for line in _candidate_lines(chunk.get("text") or ""):
            rank = _label_rank(line, aliases)
            if rank > best_rank:
                series = _series_from_chunk(chunk, aliases)
                if series:
                    best = (series, chunk)
                    best_rank = rank
    if not best:
        return None, None
    return best


def resolve_defined_formula(question, chunks):
    """Return the computed value, the calc block pieces, and the rows used."""
    match = _DEFINED.search(question or "")
    if not match or not chunks:
        return None
    formula = " ".join(match.group(1).split())
    steps = []
    inputs = []
    used = []

    def remember(chunk):
        if chunk.get("chunk_id") not in {row.get("chunk_id") for row in used}:
            used.append(chunk)

    def replace_average(found):
        operand, year_a, year_b = found.group(1), int(found.group(2)), int(found.group(3))
        series, chunk = _series(operand, chunks)
        if not series or year_a not in series or year_b not in series:
            raise ValueError(operand)
        remember(chunk)
        value = (series[year_a] + series[year_b]) / 2
        name = "average_" + _ident(operand)
        steps.append({
            "name": name,
            "expression": f"({_ident(operand)}_{year_a} + {_ident(operand)}_{year_b}) / 2",
            "value": value,
            "inputs": [(year_a, series[year_a], chunk), (year_b, series[year_b], chunk)],
            "operand": operand,
        })
        return name

    def replace_change(found):
        operand, year_a, year_b = found.group(1), int(found.group(2)), int(found.group(3))
        series, chunk = _series(operand, chunks)
        if not series or year_a not in series or year_b not in series:
            raise ValueError(operand)
        remember(chunk)
        later, earlier = max(year_a, year_b), min(year_a, year_b)
        value = series[later] - series[earlier]
        name = "change_" + _ident(operand)
        steps.append({
            "name": name,
            "expression": f"{_ident(operand)}_{later} - {_ident(operand)}_{earlier}",
            "value": value,
            "inputs": [(later, series[later], chunk), (earlier, series[earlier], chunk)],
            "operand": operand,
        })
        return name

    def replace_year(found):
        year, operand = int(found.group(1)), found.group(2)
        series, chunk = _series(operand, chunks)
        if not series or year not in series:
            raise ValueError(operand)
        remember(chunk)
        name = f"{_ident(operand)}_{year}"
        inputs.append((name, series[year], chunk))
        return name

    try:
        symbolic = _AVERAGE.sub(replace_average, formula)
        symbolic = _CHANGE.sub(replace_change, symbolic)
        symbolic = _YEAR_LINE.sub(replace_year, symbolic)
        numeric = symbolic
        replacements = [(step["name"], _fmt(step["value"])) for step in steps]
        replacements += [(name, _fmt(value)) for name, value, _chunk in inputs]
        for name, value in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
            numeric = numeric.replace(name, value)
        raw = eval_arithmetic(numeric)
    except (ValueError, SyntaxError, ZeroDivisionError):
        return None

    places = _decimal_places(question)
    rounded = _round(raw, places) if places is not None else raw
    return {
        "raw": raw,
        "rounded": rounded,
        "places": places,
        "symbolic": " ".join(symbolic.split()),
        "steps": steps,
        "inputs": inputs,
        "chunks": used,
    }


def _block(question, resolved, refs):
    lines = [f"formula: {resolved['symbolic']}"]
    seen = set()
    for step in resolved["steps"]:
        for year, value, chunk in step["inputs"]:
            name = f"{_ident(step['operand'])}_{year}"
            if name in seen:
                continue
            seen.add(name)
            lines.append(f"input: {name} = {_fmt(value)} [{refs[chunk['chunk_id']]}]")
    for name, value, chunk in resolved["inputs"]:
        if name in seen:
            continue
        seen.add(name)
        lines.append(f"input: {name} = {_fmt(value)} [{refs[chunk['chunk_id']]}]")
    for step in resolved["steps"]:
        lines.append(
            f"step: {step['name']} = {step['expression']} = {_fmt(step['value'])}"
        )
    result = _fmt(resolved["rounded"] if resolved["places"] is not None else resolved["raw"])
    lines.append(f"step: result = {resolved['symbolic']} = {result}")
    final = _fmt(resolved["rounded"])
    lines.append(f"final: {final}")
    return "\n".join(lines)


def answer_from_formula(question, chunks):
    """A verified answer when every operand of a defined formula is on a statement row."""
    resolved = resolve_defined_formula(question, chunks)
    if not resolved:
        return None

    citation_map = {}
    chunk_text = {}
    refs = {}
    for chunk in resolved["chunks"]:
        ref = f"E{len(refs) + 1}"
        refs[chunk["chunk_id"]] = ref
        citation_map[ref] = {
            "doc_id": chunk["doc_id"],
            "page": chunk["page"],
            "chunk_id": chunk["chunk_id"],
        }
        chunk_text[ref] = chunk["text"]

    shown = _fmt(resolved["rounded"])
    cited = ", ".join(refs[chunk["chunk_id"]] for chunk in resolved["chunks"])
    answer = f"{shown} [{cited}].\n```calc\n{_block(question, resolved, refs)}\n```"

    from src.answering.calculation import review_supported_answer

    reviewed = review_supported_answer(answer, question, citation_map, chunk_text)
    if reviewed["status"] != "answered":
        return None
    response = {
        "status": reviewed["status"],
        "answer": answer,
        "citations": [citation_map[ref] for ref in citation_map],
        "verification": reviewed["verification"],
        "unsupported_claims": reviewed["unsupported_claims"],
    }
    if "calculation" in reviewed:
        response["calculation"] = reviewed["calculation"]
    return response


_AMOUNT_ASK = re.compile(r"\b(what is|what was|what were|amount)\b", re.I)
_NOT_A_SINGLE_LINE = re.compile(
    r"defined\s+as|working capital|capital[-\s]intensive|\bmargin\b|\baverage\b|change in|"
    r"\bquarter\b|\bthree months\b|\bq[1-4]\b",
    re.I,
)
_FILING_UNIT = re.compile(r"\(\s*in\s+(thousands|millions|billions)\b|\bin\s+(thousands|millions|billions)\b", re.I)
_UNIT_SCALE = {"thousands": 1000, "millions": 1000000, "billions": 1000000000}
_UNIT_NAME = {1000: "thousand", 1000000: "million", 1000000000: "billion"}


def _asked_scale(question):
    low = question.lower()
    for word, scale in (("billion", 1000000000), ("million", 1000000), ("thousand", 1000)):
        if re.search(rf"\b{word}s?\b", low):
            return scale
    return None


def _filing_scale(text):
    match = _FILING_UNIT.search(text or "")
    if not match:
        return None
    word = (match.group(1) or match.group(2)).lower()
    return _UNIT_SCALE[word]


def answer_from_reported_line(question, chunks):
    """One printed statement line for one year, converted into the unit the question asks for.

    A thousands figure is not reported as millions. No average is added.
    """
    if not question or not chunks or not _AMOUNT_ASK.search(question):
        return None
    if _NOT_A_SINGLE_LINE.search(question):
        return None
    items = line_items_in(question)
    years = list(dict.fromkeys(int(year) for year in re.findall(r"(?:19|20)\d{2}", question)))
    if len(items) != 1 or len(years) != 1:
        return None

    item = items[0]
    year = years[0]
    chunk = choose_row(chunks, item, statements_in_question(question))
    if not chunk:
        return None
    series = _series_from_chunk(chunk, item["aliases"])
    if not series or year not in series:
        return None

    printed = series[year]
    filing = _filing_scale(chunk.get("text") or "")
    asked = _asked_scale(question)
    if asked is not None and filing is None:
        return None
    scale = filing or asked or 1
    target = asked or scale
    if scale % target != 0 and target % scale != 0:
        return None

    name = f"{_ident(item['phrase'])}_{year}"
    ref = "E1"
    citation_map = {
        ref: {
            "doc_id": chunk["doc_id"],
            "page": chunk["page"],
            "chunk_id": chunk["chunk_id"],
        }
    }
    chunk_text = {ref: chunk["text"]}
    places = _decimal_places(question)
    if places is None and target >= 1000000000:
        places = 2
    if places is None and target > scale:
        places = 0

    if scale == target:
        shown = _fmt(printed)
        answer = f"{shown} {_UNIT_NAME.get(target, '')} [{ref}].".replace("  ", " ")
    else:
        if target > scale:
            factor = target // scale
            expression = f"{name} / {factor}"
        else:
            factor = scale // target
            expression = f"{name} * {factor}"
        if places == 0:
            expression = f"nearest({expression})"
        raw = eval_arithmetic(expression.replace(name, _fmt(printed)))
        shown_value = _round(raw, places) if places else raw
        shown = _fmt(shown_value)
        unit = _UNIT_NAME.get(target, "")
        answer = (
            f"{shown} {unit} [{ref}].\n"
            "```calc\n"
            f"formula: {expression}\n"
            f"input: {name} = {_fmt(printed)} [{ref}]\n"
            f"step: result = {expression} = {shown}\n"
            f"final: {shown}\n"
            "```"
        )

    from src.answering.calculation import review_supported_answer

    reviewed = review_supported_answer(answer, question, citation_map, chunk_text)
    if reviewed["status"] != "answered":
        return None
    response = {
        "status": reviewed["status"],
        "answer": answer,
        "citations": [citation_map[ref]],
        "verification": reviewed["verification"],
        "unsupported_claims": reviewed["unsupported_claims"],
    }
    if "calculation" in reviewed:
        response["calculation"] = reviewed["calculation"]
    return response
