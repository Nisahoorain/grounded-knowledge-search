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

INDEX_VERSION = "bm25-v3"
CANDIDATE_POOL = 80
RRF_K = 60
BM25_K1 = 1.5
BM25_B = 0.75
PAGE_SIZE = 2000
INDEX_PATH = PROJECT_DIR / "data" / "bm25_index.pkl"

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
_FY = re.compile(r"\bfy(?=\d)")
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
    "expenditure": ("expenditures", "spending"),
    "expenditures": ("expenditure", "spending"),
    "capex": ("expenditure", "expenditures", "spending"),
}
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


def tokenize(text):
    folded = _POSSESSIVE.sub(" ", (text or "").lower().replace("’", "'"))
    folded = folded.replace("'", " ")
    folded = _FY.sub("", folded)
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
        "pages": [],
        "desks": [],
        "doc_len": [],
        "avgdl": 0.0,
        "idf": {},
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
            doc_i = len(index["chunk_ids"])
            index["chunk_ids"].append(chunk_id)
            index["doc_ids"].append(str(meta.get("doc_id") or ""))
            index["pages"].append(int(meta.get("page") or 0))
            index["desks"].append(str(meta.get("desk") or ""))
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


def _bm25(question, desks, limit, doc_ids=None):
    index = get_index()
    if not index["chunk_ids"] or index["avgdl"] <= 0:
        return []
    allowed = set(desks)
    scores = defaultdict(float)
    avgdl = index["avgdl"]
    doc_len = index["doc_len"]
    idf = index["idf"]
    postings = index["postings"]
    desks_by_doc = index["desks"]
    doc_by_i = index["doc_ids"]
    for token in _query_tokens(question):
        weight = idf.get(token)
        hits = postings.get(token)
        if not weight or not hits:
            continue
        for doc_i, freq in hits:
            if desks_by_doc[doc_i] not in allowed:
                continue
            if doc_ids is not None and doc_by_i[doc_i] not in doc_ids:
                continue
            norm = freq + BM25_K1 * (1 - BM25_B + BM25_B * doc_len[doc_i] / avgdl)
            scores[doc_i] += weight * (freq * (BM25_K1 + 1)) / norm
    if not scores:
        return []
    ranked = sorted(scores, key=scores.get, reverse=True)[:limit]
    texts = _texts([index["chunk_ids"][doc_i] for doc_i in ranked])
    hits = []
    for doc_i in ranked:
        chunk_id = index["chunk_ids"][doc_i]
        hits.append(
            {
                "doc_id": index["doc_ids"][doc_i],
                "page": index["pages"][doc_i],
                "chunk_id": chunk_id,
                "score": scores[doc_i],
                "text": texts.get(chunk_id, ""),
            }
        )
    return hits


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


def _fuse(hit_lists, limit):
    fused = defaultdict(float)
    chosen = {}
    for hits in hit_lists:
        for rank, hit in enumerate(hits, start=1):
            fused[hit["chunk_id"]] += 1.0 / (RRF_K + rank)
            chosen.setdefault(hit["chunk_id"], hit)
    ordered = sorted(fused, key=fused.get, reverse=True)[:limit]
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
    semantic_hits = _for_company(semantic_search(text, role, pool), company_docs)
    keyword_hits = _bm25(text, desks, pool, company_docs)
    statement_hits = _statement_hits(text, semantic_hits + keyword_hits, pool)
    return _fuse([semantic_hits, keyword_hits, statement_hits], limit)


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
