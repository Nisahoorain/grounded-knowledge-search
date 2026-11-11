"""Pull named statement lines into an answer, for any company.

Search still returns one row per page. A formula often needs several lines
from the same page, and the line the question names can rank below a
different use of the same words. This looks up each named line on its
statement and appends those rows. Questions that do not name a line are
unchanged.
"""

import re

from src.retrieval.query_parse import parse_query

# phrase in the question, BM25 queries, home statement, labels that count as that line
_LINES = (
    ("property, plant, and equipment", ("property plant equipment",), "balance_sheet", ("property plant and equipment", "property plant")),
    ("property, plant and equipment", ("property plant equipment",), "balance_sheet", ("property plant and equipment", "property plant")),
    ("depreciation and amortization", ("depreciation amortization",), "cash_flow", ("depreciation and amortization", "depreciation")),
    ("capital expenditures", ("capital expenditure", "capital spending"), "cash_flow", ("capital expenditure", "capital spending", "purchases of property", "payments for property")),
    ("capital expenditure", ("capital expenditure", "capital spending"), "cash_flow", ("capital expenditure", "capital spending", "purchases of property", "payments for property")),
    ("accounts payable", ("accounts payable",), "balance_sheet", ("accounts payable",)),
    ("current liabilities", ("current liabilities",), "balance_sheet", ("current liabilities",)),
    ("current assets", ("current assets",), "balance_sheet", ("current assets",)),
    ("total assets", ("total assets",), "balance_sheet", ("total assets",)),
    ("cost of goods sold", ("cost of sales",), "income", ("cost of sales", "cost of goods")),
    ("cost of sales", ("cost of sales",), "income", ("cost of sales", "cost of goods")),
    ("net income", ("net income", "net earnings", "net loss"), "income", ("net income", "net earnings", "net loss")),
    ("total revenues", ("total revenues",), "income", ("total revenues", "total revenue", "net sales", "net revenue")),
    ("total revenue", ("total revenue",), "income", ("total revenue", "total revenues", "net sales", "net revenue")),
    ("net sales", ("net sales", "revenue"), "income", ("net sales", "net revenue", "total revenues", "total revenue", "revenue")),
    ("inventories", ("inventories", "inventory"), "balance_sheet", ("inventories", "inventory")),
    ("inventory", ("inventories", "inventory"), "balance_sheet", ("inventories", "inventory")),
    ("cogs", ("cost of sales",), "income", ("cost of sales", "cost of goods")),
    ("pp&e", ("property plant equipment",), "balance_sheet", ("property plant and equipment", "property plant")),
    ("capex", ("capital expenditure",), "cash_flow", ("capital expenditure", "purchases of property", "payments for property")),
)

_METRICS = (
    (
        r"working capital",
        (
            ("current assets", ("current assets",), "balance_sheet", ("current assets",)),
            ("current liabilities", ("current liabilities",), "balance_sheet", ("current liabilities",)),
        ),
    ),
    (
        r"capital[-\s]intensive",
        (
            ("total assets", ("total assets",), "balance_sheet", ("total assets",)),
            ("net sales", ("net sales", "revenue"), "income", ("net sales", "net revenue", "total revenues", "total revenue", "revenue")),
        ),
    ),
    (
        r"\bmargin\b",
        (
            ("net income", ("net income", "net earnings", "net loss"), "income", ("net income", "net earnings", "net loss")),
            ("net sales", ("net sales", "revenue"), "income", ("net sales", "net revenue", "total revenues", "total revenue", "revenue")),
        ),
    ),
    (
        r"free cash\s*flow|cash\s*flow conversion",
        (
            (
                "net cash provided by operating activities",
                ("operating activities",),
                "cash_flow",
                (
                    "net cash provided by operating activities",
                    "cash provided by operating activities",
                ),
            ),
            (
                "capital expenditures",
                ("capital expenditure", "purchases of property"),
                "cash_flow",
                (
                    "purchases of property",
                    "capital expenditure",
                    "payments for property",
                ),
            ),
            (
                "net income",
                ("net income", "net earnings", "net loss"),
                "income",
                ("net income", "net earnings", "net loss"),
            ),
        ),
    ),
)

_AMOUNT_Q = re.compile(r"\b(what (?:is|was|were)|how much)\b")

_STATEMENT_RES = (
    ("balance_sheet", re.compile(r"balance sheets?\b")),
    ("cash_flow", re.compile(r"cash\s*flows?\b")),
    ("income", re.compile(
        r"income statements?\b|statements?\s+of\s+income\b|p&l\b|profit and loss\b|statements?\s+of\s+operations\b"
    )),
)

_NOTE = re.compile(r"\([^)]*\)")
_AMOUNT = re.compile(r"\d[\d,]*(?:\.\d+)?")
_PAGE = re.compile(r"\[page:\s*\d+\]", re.I)
_META = re.compile(r"(?i)^(company|year|document)\s*:")


def _norm(text):
    text = text.lower().replace("&", " and ")
    text = _NOTE.sub(" ", text)
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return " ".join(text.split())


def statements_in_question(question):
    low = question.lower()
    return {name for name, pattern in _STATEMENT_RES if pattern.search(low)}


def _item(phrase, queries, home, aliases):
    return {
        "phrase": phrase,
        "queries": queries,
        "home": home,
        "aliases": aliases,
    }


def line_items_in(question):
    """Lines the question itself names, plus the standard parts of a named ratio."""
    low = " " + re.sub(r"\s+", " ", question.lower()) + " "
    occupied = []
    items = []
    seen = set()
    for phrase, queries, home, aliases in sorted(_LINES, key=lambda row: len(row[0]), reverse=True):
        pattern = re.compile(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])")
        match = pattern.search(low)
        if not match:
            continue
        span = (match.start(), match.end())
        if any(not (span[1] <= start or span[0] >= end) for start, end in occupied):
            continue
        key = (home, queries)
        if key in seen:
            continue
        occupied.append(span)
        seen.add(key)
        items.append(_item(phrase, queries, home, aliases))

    for pattern, extras in _METRICS:
        if not re.search(pattern, low):
            continue
        for phrase, queries, home, aliases in extras:
            key = (home, queries)
            if key in seen:
                continue
            seen.add(key)
            items.append(_item(phrase, queries, home, aliases))

    named = statements_in_question(question)
    metric = any(re.search(pattern, low) for pattern, _extras in _METRICS)
    # Single plain line item (e.g. "What were Pfizer's total revenues for FY2022?")
    # used to return [] and skip statement-row attach / deterministic path.
    one_year = len(set(re.findall(r"(?:19|20)\d{2}", low))) == 1
    amount_q = bool(_AMOUNT_Q.search(low)) and one_year
    if len(items) >= 2 or named or metric or (len(items) == 1 and amount_q):
        return items
    return []


def _clean_line(line):
    return _PAGE.sub(" ", line)


def _candidate_lines(text):
    piped, plain = [], []
    for raw in text.splitlines():
        line = _clean_line(raw)
        if len(_AMOUNT.findall(line)) < 2:
            continue
        if "|" in line:
            piped.append(line)
        else:
            plain.append(line)
    return piped or plain


def _line_label(line):
    cells = line.split("|") if "|" in line else [line]
    for cell in cells:
        label = _norm(cell)
        if re.search(r"[a-z]", label):
            return label
    return ""


def _label_rank(line, aliases):
    label = _line_label(line)
    best = 0
    for alias in aliases:
        alias_n = _norm(alias)
        bare = label[6:].strip() if label.startswith("total ") else label
        if bare == alias_n or label == alias_n:
            rank = 4
        elif bare.startswith(alias_n + " ") or label.startswith(alias_n + " "):
            rank = 2 if label.startswith("other ") else 3
        elif alias_n in label:
            rank = 2
        else:
            rank = 0
        if rank and any(word in label for word in ("attributable", "noncontrolling", "continuing operations")):
            rank = min(rank, 2)
        if rank > best:
            best = rank
    return best


def statements_in_text(text):
    low = text.lower()
    found = set()
    if "balance sheet" in low or "financial position" in low:
        found.add("balance_sheet")
    if "cash flow" in low:
        found.add("cash_flow")
    if (
        "statement of income" in low
        or "statements of income" in low
        or "income statement" in low
        or "statement of operations" in low
        or "statements of operations" in low
        or "statements of earnings" in low
    ):
        found.add("income")
    return found


def _company_aliases(doc_id):
    from src.retrieval.query_parse import ALIASES

    prefix = (doc_id or "").split("_")[0]
    return [alias.lower() for alias in ALIASES.get(prefix, ())]


def _names_company(text, doc_id):
    body = " ".join(
        line for line in text.splitlines()
        if not _META.match(line.strip())
    ).lower()
    for alias in _company_aliases(doc_id):
        if re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", body):
            return 1
    return 0


def _distinct_amounts(text, aliases):
    found = []
    for line in _candidate_lines(text):
        if _label_rank(line, aliases) == 0:
            continue
        found.extend(token.replace(",", "") for token in _AMOUNT.findall(line))
    return len(set(found))


def _is_amountish(line):
    return re.fullmatch(r"[\d$,.\s\(\)\-—–]+", line.strip() or "") is not None


def _loose_label(text, aliases):
    """A statement line whose amounts sit on the next lines, not in one pipe row."""
    lines = text.splitlines()
    best_rank = 0
    best_amounts = []
    for index, line in enumerate(lines):
        if "|" in line:
            continue
        rank = _label_rank(line.strip(), aliases)
        if rank < 3:
            continue
        amounts = []
        for nxt in lines[index + 1:index + 12]:
            if not nxt.strip():
                continue
            if not _is_amountish(nxt):
                break
            amounts.extend(_AMOUNT.findall(nxt))
        if len(amounts) < 2 or rank <= best_rank:
            continue
        best_rank = rank
        best_amounts = amounts
    return best_rank, best_amounts


def choose_row(hits, item, named_statements):
    """Prefer the statement the question named. Fall back to the row label."""
    scored = []
    for order, hit in enumerate(hits):
        text = hit.get("text") or ""
        rank = 0
        for line in _candidate_lines(text):
            rank = max(rank, _label_rank(line, item["aliases"]))
        piped = rank > 0
        loose_amounts = []
        if rank < 3:
            loose_rank, loose_amounts = _loose_label(text, item["aliases"])
            rank = max(rank, loose_rank)
        if rank == 0:
            continue
        present = statements_in_text(text)
        home = item["home"]
        if named_statements and home in named_statements:
            headed = home in present
        elif named_statements:
            headed = bool(present & named_statements)
        else:
            headed = bool(present)
        distinct = _distinct_amounts(text, item["aliases"])
        if loose_amounts:
            distinct = max(distinct, len({token.replace(",", "") for token in loose_amounts}))
        scored.append((
            headed,
            piped,
            _names_company(text, hit.get("doc_id") or ""),
            rank,
            distinct,
            -order,
            hit,
        ))
    if not scored:
        return None
    if any(row[0] for row in scored):
        scored = [row for row in scored if row[0]]
    scored.sort(reverse=True)
    return scored[0][-1]


def _docs_for(question):
    parsed = parse_query(question)
    docs = parsed["docs"]
    if not parsed["companies"] or not docs:
        return []
    if parsed["years"]:
        latest = max(parsed["years"])
        latest_docs = [doc for doc in docs if doc["year"] == latest]
        if latest_docs:
            docs = latest_docs
    return docs


def find_statement_rows(question, role, search=None):
    items = line_items_in(question)
    docs = _docs_for(question)
    if not items or not docs:
        return []
    if search is None:
        from src.retrieval.fts_store import search_bm25
        search = search_bm25
    doc_ids = [doc["doc_id"] for doc in docs]
    named = statements_in_question(question)
    chosen = []
    seen = set()
    for item in items:
        fused = {}
        for query in item["queries"]:
            for rank, hit in enumerate(search(query, role, 50, doc_ids)):
                cid = hit.get("chunk_id")
                if cid not in fused or rank < fused[cid][0]:
                    fused[cid] = (rank, hit)
        hits = [hit for _rank, hit in sorted(fused.values(), key=lambda pair: pair[0])]
        hit = choose_row(hits, item, named)
        if hit and hit.get("chunk_id") not in seen:
            seen.add(hit["chunk_id"])
            tagged = dict(hit)
            tagged["line_item"] = item["phrase"]
            chosen.append(tagged)
    return chosen


def _defined_metric_labels(question):
    """The printed ratio a 'defined as' question is replacing with its own formula."""
    match = re.search(r"defined\s+as\b", question, re.I)
    if not match:
        return []
    before = question[:match.start()].lower()
    labels = []
    for phrase, acronym in re.findall(r"([a-z][a-z ]{3,80})\s*\(([a-z0-9]{2,12})\)", before):
        labels.append(_norm(phrase))
        labels.append(acronym)
    return [label for label in labels if label]


def aliases_for_operand(operand):
    """Longest known line whose name appears in a formula operand."""
    op = _norm(operand)
    best = None
    best_len = -1
    for phrase, _queries, _home, aliases in _LINES:
        for name in (_norm(phrase), *(_norm(alias) for alias in aliases)):
            if op == name and len(name) > best_len:
                best = aliases
                best_len = len(name)
    return best


def without_printed_metric(question, results):
    """Drop the filing's own ratio when the question defines a different formula."""
    labels = _defined_metric_labels(question)
    if not labels:
        return list(results)
    kept = []
    for row in results:
        text = row.get("text") or ""
        printed = False
        for line in _candidate_lines(text):
            if _label_rank(line, labels) >= 3:
                printed = True
                break
        if not printed:
            kept.append(row)
    return kept or list(results)


def attach_statement_rows(question, role, results, search=None):
    """Keep the ranked list, then add any named statement row it does not already have."""
    merged = list(results)
    seen = {row.get("chunk_id") for row in merged}
    for row in find_statement_rows(question, role, search=search):
        if row.get("chunk_id") not in seen:
            merged.append(row)
            seen.add(row["chunk_id"])
    return without_printed_metric(question, merged)
