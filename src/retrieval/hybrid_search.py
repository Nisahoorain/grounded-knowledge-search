"""Hybrid retrieval: semantic search plus BM25, fused with reciprocal rank fusion.

Two corpus-wide rules apply to every question. A named company keeps both
lists inside that company's filings. A named statement adds a third list of
chunks from pages that actually contain that statement heading. The returned
score is the fused rank score, so a higher score is more relevant. It is not
a cosine similarity.
"""

import csv
import math
import os
import pickle
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.access_control import desks_for
from src.config import MANIFEST_PATH, TOP_K
from src.ingestion.chunker import CHUNKING_VERSION
from src.retrieval.search import search as semantic_search
from src.retrieval.vector_store import collection_count, get_collection

INDEX_VERSION = "bm25-v6"
CANDIDATE_POOL = 120
RRF_K = 60
BM25_K1 = 1.5
BM25_B = 0.75
PAGE_SIZE = 2000
INDEX_PATH = PROJECT_DIR / "data" / "bm25_index.pkl"

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
_FY = re.compile(r"\bfy\s*(\d{2,4})\b", re.I)
_COMMA = re.compile(r"(?<=\d),(?=\d)")
_POSSESSIVE = re.compile(r"'s\b")
STOPWORDS = frozenset(
    """
    a an the of and or to for in on by with from as at into over under
    is are was were be been being what which who whom whose how when where why
    did does do has have had if then than that this these those it its their
    our your we you they them not no nor can could should would may might will
    only within between about across per question answer respond please
    following according based using used shown details information provided
    primarily clearly outlined assuming perspective analyst judgment judgments
    amount amounts basing off calculate defined round decimal place places
    unit units percent percents million millions billion billions usd end
    """.split()
)

# Abbreviations of a manifest company. These are names of the company, not of a question.
_ALIASES = {
    "Johnson & Johnson": ("J&J", "JnJ", "JNJ"),
    "MGM Resorts": ("MGM",),
    "PepsiCo": ("Pepsi",),
    "Ulta Beauty": ("Ulta",),
}
_QUERY_EXPANSIONS = {
    "capex": ("expenditure", "expenditures", "spending", "purchases"),
    "expenditure": ("expenditures", "spending", "purchases"),
    "expenditures": ("expenditure", "spending", "purchases"),
    "spending": ("expenditure", "expenditures", "purchases"),
    "revenue": ("revenues", "sales"),
    "revenues": ("revenue", "sales"),
    "sales": ("revenue", "revenues"),
    "customers": ("customer", "clients"),
    "customer": ("customers", "client", "clients"),
    "payable": ("payables",),
    "payables": ("payable",),
    "dividend": ("dividends",),
    "dividends": ("dividend",),
    "inventory": ("inventories",),
    "inventories": ("inventory",),
}
_FORWARD_LOOKING = re.compile(
    r"\b(?:forecast\w*|outlook|guidance|anticipat\w*)\b",
    re.I,
)
_TABLE_QUESTION = re.compile(
    r"balance\s+sheets?|income\s+statement|statements?\s+of\s+(?:income|operations|cash)"
    r"|cash\s*-?\s*flows?|p&l|profit and loss|amount|calculate|ratio|margin"
    r"|expenditure|depreciation|working capital|accounts payable",
    re.I,
)
_TEXT_QUESTION = re.compile(
    r"\b(?:did|does|has|have|drove|driven|forecast\w*|compar\w*|versus|why|whether|registered)\b",
    re.I,
)
_CONTENTS_ITEM = re.compile(r"(?i)\bitem\s+\d+[a-z]?\b")
_CHUNK_KIND = re.compile(r":p\d+:(text|table):")
_STATEMENT_LABELS = (
    ("cash_flow", re.compile(r"statements?\s+of\s+cash\s+flows", re.I)),
    (
        "balance_sheet",
        re.compile(r"balance\s+sheets?|statements?\s+of\s+financial\s+position", re.I),
    ),
    ("income", re.compile(r"statements?\s+of\s+(?:income|operations|earnings)|income\s+statements?", re.I)),
)
# Line items that belong to a statement even when the question does not name the statement.
_CONCEPT_STATEMENTS = (
    ("cash_flow", re.compile(r"capital\s+expenditures?|\bcapex\b", re.I)),
)
_QUESTION_STATEMENTS = (
    ("cash_flow", re.compile(r"cash\s*-?\s*flows?", re.I)),
    ("balance_sheet", re.compile(r"balance\s+sheets?", re.I)),
    ("income", re.compile(r"income\s+statement|statements?\s+of\s+(?:income|operations)|p&l|profit and loss", re.I)),
)

_index = None
_companies = None

def _normalize_fy(match):
    year = match.group(1)
    if len(year) == 2:
        year = f"20{year}" if int(year) <= 49 else f"19{year}"
    return f" {year} "


def _requested_years(question):
    """Extract explicit FY years and expand FY year ranges strictly."""
    text = question or ""
    years = set()
    for match in _FY.finditer(text):
        year = _normalize_fy(match).strip()
        years.add(int(year))
    range_pattern = re.compile(
        r"\bfy\s*(\d{2,4})\s*(?:[-–—]|\bto\b)\s*(?:fy\s*)?(\d{2,4})\b",
        re.I,
    )
    for match in range_pattern.finditer(text):
        start = _normalize_fy(
            re.match(r"(\d{2,4})", match.group(1))
        ).strip()
        end = _normalize_fy(
            re.match(r"(\d{2,4})", match.group(2))
        ).strip()
        start, end = int(start), int(end)
        if start <= end and end - start <= 20:
            years.update(range(start, end + 1))
    return years


def tokenize(text):
    folded = _POSSESSIVE.sub(" ", (text or "").lower().replace("’", "'"))
    folded = folded.replace("'", " ")
    folded = _FY.sub(_normalize_fy, folded)
    folded = _COMMA.sub("", folded)
    return [
        token
        for token in _TOKEN.findall(folded)
        if len(token) > 1 and token not in STOPWORDS
    ]


def _stamp():
    return f"{INDEX_VERSION}:{CHUNKING_VERSION}:{collection_count()}"


def _empty_index(stamp):
    return {
        "stamp": stamp,
        "chunk_ids": [],
        "doc_ids": [],
        "periods": [],
        "pages": [],
        "desks": [],
        "content_types": [],
        "doc_len": [],
        "avgdl": 0.0,
        "idf": {},
        "avgdl_by_type": {},
        "idf_by_type": {},
        "postings": {},
        "page_statements": {},
    }


def build_index():
    stamp = _stamp()
    collection = get_collection()
    total = collection.count()
    index = _empty_index(stamp)
    if total == 0:
        return index

    postings = defaultdict(list)
    type_doc_freq = {
        "text": Counter(),
        "table": Counter(),
    }
    offset = 0
    while offset < total:
        batch = collection.get(
            include=["documents", "metadatas"],
            limit=PAGE_SIZE,
            offset=offset,
        )
        ids = batch.get("ids") or []
        if not ids:
            break
        documents = batch.get("documents") or []
        metadatas = batch.get("metadatas") or []
        for chunk_id, document, meta in zip(ids, documents, metadatas):
            meta = meta or {}
            counts = Counter(tokenize(document or ""))
            content_type = str(meta.get("content_type") or "unknown")
            if content_type in type_doc_freq:
                type_doc_freq[content_type].update(counts.keys())
            doc_i = len(index["chunk_ids"])
            index["chunk_ids"].append(chunk_id)
            index["doc_ids"].append(str(meta.get("doc_id") or ""))
            index["periods"].append(str(meta.get("period") or ""))
            index["pages"].append(int(meta.get("page") or 0))
            index["desks"].append(str(meta.get("desk") or ""))
            index["content_types"].append(
                str(meta.get("content_type") or "unknown")
            )
            index["doc_len"].append(sum(counts.values()))
            for token, freq in counts.items():
                postings[token].append((doc_i, freq))
            labels = [
                name
                for name, pattern in _STATEMENT_LABELS
                if pattern.search(document or "")
            ]
            if labels:
                key = f"{index['doc_ids'][-1]}:{index['pages'][-1]}"
                found = index["page_statements"].setdefault(key, [])
                for label in labels:
                    if label not in found:
                        found.append(label)
        offset += len(ids)
        print(f"bm25 index: {offset}/{total}", flush=True)

    lengths = index["doc_len"]
    index["avgdl"] = (sum(lengths) / len(lengths)) if lengths else 0.0
    n_docs = len(lengths)
    index["idf"] = {
        token: math.log((n_docs - len(hits) + 0.5) / (len(hits) + 0.5) + 1.0)
        for token, hits in postings.items()
    }
    index["avgdl_by_type"] = {}
    for kind in ("text", "table"):
        lengths = [
            length
            for length, content in zip(
                index["doc_len"], index["content_types"]
            )
            if content == kind
        ]
        n_docs = len(lengths)
        index["avgdl_by_type"][kind] = (
            sum(lengths) / n_docs if n_docs else 0.0
        )
        index["idf_by_type"][kind] = {
            token: math.log(
                (n_docs - df + 0.5) / (df + 0.5) + 1.0
            )
            for token, df in type_doc_freq[kind].items()
        }
    index["postings"] = dict(postings)
    _save_index(index)
    print(f"bm25 index saved: {len(index['chunk_ids'])} chunks", flush=True)
    return index


def _save_index(index):
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_PATH.with_suffix(".pkl.tmp")
    with open(tmp, "wb") as file:
        pickle.dump(index, file, protocol=pickle.HIGHEST_PROTOCOL)
    for _ in range(5):
        try:
            os.replace(tmp, INDEX_PATH)
            return
        except PermissionError:
            time.sleep(0.2)
    with open(INDEX_PATH, "wb") as file:
        pickle.dump(index, file, protocol=pickle.HIGHEST_PROTOCOL)
    try:
        tmp.unlink()
    except OSError:
        pass


def get_index():
    global _index
    stamp = _stamp()
    if _index is not None and _index.get("stamp") == stamp:
        return _index
    if INDEX_PATH.exists():
        try:
            with open(INDEX_PATH, "rb") as file:
                loaded = pickle.load(file)
            if isinstance(loaded, dict) and loaded.get("stamp") == stamp:
                _index = loaded
                return _index
        except (OSError, pickle.UnpicklingError, EOFError):
            pass
    _index = build_index()
    return _index


def _query_tokens(question):
    tokens = set(tokenize(question))
    expanded = set(tokens)
    for token in tokens:
        expanded.update(_QUERY_EXPANSIONS.get(token, ()))
    return expanded


def corpus_companies():
    global _companies
    if _companies is not None:
        return _companies
    grouped = defaultdict(set)
    with open(MANIFEST_PATH, encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            grouped[row["company"]].add(row["doc_id"])
    compiled = []
    for company, doc_ids in grouped.items():
        names = (company,) + _ALIASES.get(company, ())
        patterns = [re.compile(rf"\b{re.escape(name)}\b", re.I) for name in names]
        compiled.append((company, doc_ids, patterns))
    _companies = compiled
    return _companies


def named_company(question):
    """Return the doc_ids of the one company named in the question, if there is one."""
    matched = []
    for company, doc_ids, patterns in corpus_companies():
        if any(pattern.search(question) for pattern in patterns):
            matched.append(doc_ids)
    if len(matched) != 1:
        return None
    return matched[0]


def _question_statements(question):
    text = question or ""
    labels = {
        label
        for label, pattern in _QUESTION_STATEMENTS
        if pattern.search(text)
    }
    for label, pattern in _CONCEPT_STATEMENTS:
        if pattern.search(text):
            labels.add(label)
    return labels


def _for_company(hits, doc_ids):
    if not doc_ids:
        return hits
    return [hit for hit in hits if hit["doc_id"] in doc_ids]


def _forward_looking(question):
    return bool(_FORWARD_LOOKING.search(question or ""))


def _evidence_kind(question):
    """Choose table evidence or narrative evidence for this question."""
    text = question or ""
    if _question_statements(text) or _TABLE_QUESTION.search(text):
        return "table"
    if _TEXT_QUESTION.search(text):
        return "text"
    return None


def _chunk_kind(hit):
    kind = str(hit.get("content_type") or "")
    if kind in ("text", "table"):
        return kind
    match = _CHUNK_KIND.search(hit.get("chunk_id") or "")
    return match.group(1) if match else ""



def _is_contents(text):
    text = text or ""
    normalized = re.sub(r"\s+", " ", text).strip().lower()

    if not normalized:
        return False

    # Explicit table-of-contents headings are strong indicators.
    explicit_toc = bool(
        re.search(
            r"\btable\s+of\s+contents\b|\bcontents\s*\(continued\)",
            normalized,
        )
    )
    if explicit_toc:
        return True

    # Detect filing navigation pages using repeated item headings
    # together with page-number / section-listing structure.
    items = _CONTENTS_ITEM.findall(text)
    if len(items) < 8:
        return False

    has_parts = bool(
        re.search(r"\bpart\s+(?:i{1,3}|iv|[1-4])\b", normalized)
    )
    has_page_header = bool(
        re.search(r"\b(?:beginning\s+)?page\b", normalized)
    )

    # TOC pages usually contain many short heading/page-number entries.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    short_lines = sum(
        1 for line in lines
        if len(line.split()) <= 8
    )
    short_line_ratio = short_lines / max(len(lines), 1)

    return has_parts and len(items) >= 8 and short_line_ratio >= 0.45



def _matching_period(hits, years):
    if not years:
        return hits
    chosen = [hit for hit in hits if str(hit.get("period") or "") in years]
    return chosen or hits


def _drop_contents(hits):
    chosen = [hit for hit in hits if not _is_contents(hit.get("text") or "")]
    return chosen or hits


def _prefer_kind(hits, kind):
    if not kind:
        return hits
    chosen = [hit for hit in hits if _chunk_kind(hit) == kind]
    return chosen or hits


def _bm25(question, desks, limit, doc_ids=None, content_type=None, periods=None):
    index = get_index()
    if not index["chunk_ids"]:
        return []
    if content_type is not None:
        avgdl = index.get("avgdl_by_type", {}).get(content_type, 0.0)
        idf = index.get("idf_by_type", {}).get(content_type, {})
    else:
        avgdl = index["avgdl"]
        idf = index["idf"]
    if avgdl <= 0:
        return []
    allowed = set(desks)
    scores = defaultdict(float)
    doc_len = index["doc_len"]
    postings = index["postings"]
    desks_by_doc = index["desks"]
    doc_by_i = index["doc_ids"]
    content_types = index["content_types"]
    periods_by_doc = index.get("periods") or []
    period_filter = set(periods) if periods and periods_by_doc else None
    for token in _query_tokens(question):
        weight = idf.get(token)
        hits = postings.get(token)
        if not weight or not hits:
            continue
        for doc_i, freq in hits:
            if desks_by_doc[doc_i] not in allowed:
                continue
            if content_type is not None and content_types[doc_i] != content_type:
                continue
            if period_filter is not None and periods_by_doc[doc_i] not in period_filter:
                continue
            if doc_ids is not None and doc_by_i[doc_i] not in doc_ids:
                continue
            norm = freq + BM25_K1 * (1 - BM25_B + BM25_B * doc_len[doc_i] / avgdl)
            scores[doc_i] += weight * (freq * (BM25_K1 + 1)) / norm
    if not scores:
        return []
    ranked = sorted(scores, key=scores.get, reverse=True)[: max(limit * 4, limit)]
    texts = _texts([index["chunk_ids"][doc_i] for doc_i in ranked])
    hits = []
    for doc_i in ranked:
        chunk_id = index["chunk_ids"][doc_i]
        hits.append(
            {
                "doc_id": index["doc_ids"][doc_i],
                "period": index["periods"][doc_i],
                "page": index["pages"][doc_i],
                "chunk_id": chunk_id,
                "score": scores[doc_i],
                "text": texts.get(chunk_id, ""),
            }
        )
    kept = [hit for hit in hits if not _is_contents(hit["text"])]
    return (kept or hits)[:limit]


def _texts(chunk_ids):
    if not chunk_ids:
        return {}
    found = get_collection().get(ids=list(chunk_ids), include=["documents"])
    return {
        chunk_id: body or ""
        for chunk_id, body in zip(found.get("ids") or [], found.get("documents") or [])
    }


def _statement_hits(question, ranked_hits, limit):
    labels = _question_statements(question)
    pages = get_index().get("page_statements") or {}
    if not labels or not pages:
        return []
    chosen = []
    seen = set()
    for hit in ranked_hits:
        key = f"{hit['doc_id']}:{hit['page']}"
        if not labels.intersection(pages.get(key, ())):
            continue
        if hit["chunk_id"] in seen:
            continue
        seen.add(hit["chunk_id"])
        chosen.append(hit)
        if len(chosen) >= limit:
            break
    return chosen


def _fuse(hit_lists, limit, requested_years=None, prefer_table=False):
    fused = defaultdict(float)
    chosen = {}
    for hits in hit_lists:
        for rank, hit in enumerate(hits, start=1):
            fused[hit["chunk_id"]] += 1.0 / (RRF_K + rank)
            chosen.setdefault(hit["chunk_id"], hit)
            
    def ranking_score(chunk_id):
        hit = chosen[chunk_id]
        score = fused[chunk_id]
        period = str(hit.get("period") or "")
        chunk_id_str = str(hit.get("chunk_id") or "")
        
        # Strict year matching boost
        if requested_years and period in {str(year) for year in requested_years}:
            score += 0.05
            
        # Table preference boost for financial table questions
        if prefer_table and ":table:" in chunk_id_str:
            score += 0.03
            
        return score

    ordered = sorted(
        fused,
        key=ranking_score,
        reverse=True,
    )[:limit]
    
    hits = []
    for chunk_id in ordered:
        hit = dict(chosen[chunk_id])
        hit["score"] = fused[chunk_id]
        hits.append(hit)
    return hits


def search(question, role, k=None):
    """Return the top-k chunks for a role, using semantic search and BM25.

    Results stay inside the role's desks. When the question names one company
    from the manifest, both lists are limited to that company. When it names a
    financial statement, pages containing that statement heading get a third
    vote. ``score`` is the reciprocal-rank fusion. A higher score is more relevant.
    """
    text = (question or "").strip()
    if not text:
        raise ValueError("question is required")
    limit = TOP_K if k is None else int(k)
    if limit < 1:
        raise ValueError("k must be at least 1")

    pool = max(limit, CANDIDATE_POOL)
    desks = desks_for(role)
    company_docs = named_company(text)
    years = {str(year) for year in _requested_years(text)}
    year_lock = set() if _forward_looking(text) else years
    kind = _evidence_kind(text)

    semantic_hits = semantic_search(
        text,
        role,
        pool * (5 if year_lock else 2),
        company_docs=company_docs,
        periods=year_lock or None,
    )
    semantic_hits = _prefer_kind(
        _drop_contents(_matching_period(semantic_hits, year_lock)),
        kind,
    )[:pool]

    text_hits = _bm25(
        text, desks, pool, company_docs, content_type="text", periods=year_lock or None
    )
    table_hits = _bm25(
        text, desks, pool, company_docs, content_type="table", periods=year_lock or None
    )

    hit_lists = [semantic_hits]
    if kind != "table":
        hit_lists.append(text_hits)
    if kind != "text":
        hit_lists.append(table_hits)
    hit_lists.append(_statement_hits(text, [hit for group in hit_lists for hit in group], pool))
    
    return _fuse(
        hit_lists, 
        limit, 
        requested_years=year_lock or None, 
        prefer_table=bool(_evidence_kind(text) == "table")
    )

if __name__ == "__main__":
    import json

    if len(sys.argv) < 3 or sys.argv[1] != "--role":
        print('Usage: python src/retrieval/hybrid_search.py --role <role> [--k N] "question"')
        sys.exit(1)
    role = sys.argv[2]
    args = sys.argv[3:]
    k = None
    if args and args[0] == "--k":
        k = int(args[1])
        args = args[2:]
    question = " ".join(args).strip()
    print(json.dumps(search(question, role, k), indent=2))