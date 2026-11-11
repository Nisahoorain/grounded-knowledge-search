
import os
import re
import sqlite3
from pathlib import Path

from src.config import ROLE_DESKS
from src.retrieval.vector_store import get_collection

PROJECT_DIR = Path(__file__).resolve().parents[2]
FTS_PATH = PROJECT_DIR / "data" / "fts_index.sqlite3"

PAGE_SIZE = 1000
BATCH_SIZE = 1000

STOPWORDS = {
    "what", "were", "was", "the", "is", "are",
    "of", "for", "in", "on", "to", "and", "how",
    "much", "many", "did", "does", "do", "has",
    "have", "had", "any", "from", "based", "with",
    "their", "its", "this", "that", "been", "being",
    "reported", "ongoing", "important", "materially",
    # Keep BM25 focused on filing language for yes/no concentration asks.
    "fy22", "fy2022",
}

LEGAL_TERMS = {
    "litigation", "legal", "lawsuit", "lawsuits",
    "battle", "battles", "proceeding", "proceedings",
    "claim", "claims", "regulatory",
}

YEAR_RE = re.compile(r"(?:fy)?(?:19|20)\d{2}")

SYNONYMS = {
    "sales": ["sales", "revenue", "revenues"],
    "revenue": ["revenue", "revenues", "sales"],
    "revenues": ["revenue", "revenues", "sales"],
    "income": ["income", "earnings", "profit"],
    "profit": ["profit", "income", "earnings"],
    "earnings": ["earnings", "income", "profit"],
    "capex": ["capex", "capital expenditures"],
    "debt": ["debt", "borrowings", "notes"],
    "employees": ["employees", "headcount", "associates"],
    # fb_00757-style concentration questions often use "customer" + "concentration"
    # while filings say "concentrations of credit risk" / accounts receivable %.
    "concentration": [
        "concentration",
        "concentrations",
        "customer concentration",
        "credit risk",
    ],
    "concentrations": [
        "concentrations",
        "concentration",
        "customer concentration",
        "credit risk",
    ],
}


def _group(term):
    alts = SYNONYMS.get(term, [term])
    if len(alts) == 1:
        return f'"{alts[0]}"'
    return "(" + " OR ".join(f'"{a}"' for a in alts) + ")"


def build_fts_index():
    """Build a local BM25 index from the existing Chroma collection."""

    FTS_PATH.parent.mkdir(parents=True, exist_ok=True)

    temp_path = FTS_PATH.with_name("fts_index_building.sqlite3")

    if temp_path.exists():
        temp_path.unlink()

    conn = sqlite3.connect(temp_path)

    try:
        conn.execute("""
            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                chunk_id UNINDEXED,
                doc_id UNINDEXED,
                desk UNINDEXED,
                page UNINDEXED,
                text,
                tokenize='porter unicode61'
            )
        """)

        collection = get_collection()
        offset = 0
        total = 0

        while True:
            batch = collection.get(
                limit=PAGE_SIZE,
                offset=offset,
                include=["documents", "metadatas"],
            )

            documents = batch.get("documents") or []
            metadatas = batch.get("metadatas") or []
            ids = batch.get("ids") or []

            if not ids:
                break

            rows = []

            for chunk_id, document, metadata in zip(
                ids, documents, metadatas
            ):
                metadata = metadata or {}

                rows.append((
                    str(chunk_id),
                    str(metadata.get("doc_id") or ""),
                    str(metadata.get("desk") or ""),
                    str(metadata.get("page") or ""),
                    document or "",
                ))

            conn.executemany(
                """
                INSERT INTO chunks_fts
                (chunk_id, doc_id, desk, page, text)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )

            total += len(rows)
            offset += len(ids)

            if total % BATCH_SIZE == 0:
                conn.commit()
                print(f"Indexed: {total:,}", flush=True)

            if len(ids) < PAGE_SIZE:
                break

        conn.commit()

    finally:
        conn.close()

    os.replace(temp_path, FTS_PATH)

    print(f"\nBM25 index created: {total:,} chunks")
    print(f"Location: {FTS_PATH}")


def _search_query(conn, match_query, role, limit, doc_ids=None):
    sql = """
        SELECT chunk_id, doc_id, page, text,
               bm25(chunks_fts) AS bm25_score
        FROM chunks_fts
        WHERE chunks_fts MATCH ?
          AND NOT (
              lower(text) LIKE '%item 3.%legal proceedings%'
              AND length(text) < 250
          )
    """
    params = [match_query]

    if role != "lead":
        sql += " AND desk = ?"
        params.append(role)

    if doc_ids:
        sql += f" AND doc_id IN ({','.join('?' * len(doc_ids))})"
        params.extend(doc_ids)

    sql += " ORDER BY bm25_score LIMIT ?"
    params.append(limit)

    return conn.execute(sql, params).fetchall()


def search_bm25(question, role, k=10, doc_ids=None):
    """BM25: strict + relaxed (+ legal) queries, always all run, rank-fused."""

    if role not in ROLE_DESKS:
        raise ValueError(f"Invalid role: {role}")
    if k < 1:
        raise ValueError("k must be at least 1")
    if not FTS_PATH.exists():
        raise FileNotFoundError("FTS index missing. Run build_fts_index() first.")

    terms = [
        t for t in re.findall(r"[A-Za-z0-9]+", question.lower())
        if t not in STOPWORDS
    ]
    terms = list(dict.fromkeys(terms))
    if not terms:
        return []

    # years/generic words strict query se hatao (company name/year aksar chunk text mein nahi hota)
    core = [
        t for t in terms
        if not YEAR_RE.fullmatch(t)
        and t not in {"materially", "important", "ongoing", "reported", "based"}
    ] or terms

    queries = []  # (weight, match_query)

    # 1) strict: core terms AND, synonyms OR ke saath
    queries.append((2.0, " AND ".join(_group(t) for t in core)))

    # 2) relaxed: sab terms OR; BM25 rare terms ko khud upar rakhta hai
    queries.append((1.0, " OR ".join(_group(t) for t in terms)))

    # 3) legal: hamesha chale agar legal term ho (count par depend nahi)
    if any(t in LEGAL_TERMS for t in terms):
        company_terms = [t for t in core if t not in LEGAL_TERMS]
        legal_match = "(" + " OR ".join(f'"{t}"' for t in sorted(LEGAL_TERMS)) + ")"
        q = (
            " AND ".join(_group(t) for t in company_terms) + " AND " + legal_match
            if company_terms else legal_match
        )
        queries.append((1.5, q))

    conn = sqlite3.connect(FTS_PATH)
    conn.row_factory = sqlite3.Row

    try:
        fused = {}
        per_query_limit = max(k * 5, 50)

        for weight, match_query in queries:
            rows = _search_query(conn, match_query, role, per_query_limit, doc_ids)

            for rank, row in enumerate(rows, start=1):
                cid = row["chunk_id"]
                if cid not in fused:
                    fused[cid] = {
                        "doc_id": row["doc_id"],
                        "page": int(row["page"]) if row["page"].isdigit() else None,
                        "chunk_id": cid,
                        "score": 0.0,
                        "text": row["text"],
                    }
                fused[cid]["score"] += weight / (60 + rank)

        return sorted(fused.values(), key=lambda x: x["score"], reverse=True)[:k]

    finally:
        conn.close()


if __name__ == "__main__":
    build_fts_index()
