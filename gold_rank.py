"""Where does the gold page land? Run from the project root:

    python gold_rank.py eval.jsonl
    python gold_rank.py eval.jsonl --misses
    python gold_rank.py eval.jsonl --rerank

Columns:
  doc_in_filter    gold doc survives parse_query filter
  ask@5            rank in retrieve_for_ask (no rerank)
  ask@5+rr         same with hybrid rerank=True
  pool(dedupe)     rank in 100-deep hybrid, page dedupe on
  pool+rr          same with rerank=True
  pool(no dedupe)  page_dedupe=0
  dense/bm25_rank  ranks on the non-rerank pooled hit
"""
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.retrieval.hybrid import search_hybrid
from src.retrieval.pipeline import retrieve_for_ask
from src.retrieval.query_parse import parse_query

POOL = 100


def first_rank(results, doc_id, pages):
    for i, r in enumerate(results, 1):
        if r["doc_id"] == doc_id and r["page"] in pages:
            return i, r
    return None, None


def fmt(value):
    return "-" if value is None else str(value)


def main(path, misses_only, with_rerank):
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    header = [
        "id",
        "doc_in_filter",
        "ask@5",
        "pool(dedupe)",
        "pool(no dedupe)",
        "dense_rank",
        "bm25_rank",
    ]
    if with_rerank:
        header[2:2] = ["ask@5+rr"]
        header.insert(4, "pool+rr")
    print(" | ".join(header), flush=True)

    for row in rows:
        note = row.get("_eval_note") or {}
        cited = note.get("cited_gold", row.get("cited_gold"))
        if misses_only and cited:
            continue
        question, role = row["question"], row["role"]
        gold_doc, gold_pages = row["doc_id"], set(row["pages"])

        docs = parse_query(question)["docs"]
        in_filter = (not docs) or gold_doc in {d["doc_id"] for d in docs}

        ask, _ = first_rank(
            retrieve_for_ask(question, role, k=5), gold_doc, gold_pages
        )
        ask_rr = None
        if with_rerank:
            ask_rr, _ = first_rank(
                retrieve_for_ask(question, role, k=5, rerank=True),
                gold_doc,
                gold_pages,
            )

        pooled = search_hybrid(question, role, k=POOL, candidate_k=POOL)
        rank_dedupe, hit = first_rank(pooled, gold_doc, gold_pages)
        rank_rr = None
        if with_rerank:
            pooled_rr = search_hybrid(
                question, role, k=POOL, candidate_k=POOL, rerank=True
            )
            rank_rr, _ = first_rank(pooled_rr, gold_doc, gold_pages)

        raw = search_hybrid(
            question, role, k=POOL, candidate_k=POOL, page_dedupe=0
        )
        rank_raw, _ = first_rank(raw, gold_doc, gold_pages)

        cols = [
            row["id"],
            str(in_filter),
        ]
        if with_rerank:
            cols.append(fmt(ask_rr))
        cols.append(fmt(ask))
        if with_rerank:
            cols.append(fmt(rank_rr))
        cols.append(fmt(rank_dedupe))
        cols.extend([
            fmt(rank_raw),
            fmt(hit.get("dense_rank") if hit else None),
            fmt(hit.get("bm25_rank") if hit else None),
        ])
        print(" | ".join(cols), flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    args = sys.argv[2:]
    main(sys.argv[1], "--misses" in args, "--rerank" in args)