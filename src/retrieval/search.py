# Phase 5 — baseline retrieval

import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.access_control import desks_for
from src.config import TOP_K
from src.retrieval.embeddings import embed_texts
from src.retrieval.vector_store import get_collection


def search(question, role, k=None, company_docs=None, periods=None):
    """Return top-k chunks scoped to the user's desk and, when identified,
    the requested company and reporting year.
    """
    text = (question or "").strip()
    if not text:
        raise ValueError("question is required")

    limit = TOP_K if k is None else int(k)
    if limit < 1:
        raise ValueError("k must be at least 1")

    desks = desks_for(role)

    conditions = [
        {"desk": desks[0]} if len(desks) == 1
        else {"desk": {"$in": desks}}
    ]

    if company_docs:
        conditions.append({"doc_id": {"$in": sorted(company_docs)}})

    if periods:
        conditions.append({"period": {"$in": sorted(periods)}})

    where = (
        conditions[0]
        if len(conditions) == 1
        else {"$and": conditions}
    )
    vector = embed_texts([text])[0]

    found = get_collection().query(
        query_embeddings=[vector],
        n_results=limit,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    ids = (found.get("ids") or [[]])[0]
    distances = (found.get("distances") or [[]])[0]
    metadatas = (found.get("metadatas") or [[]])[0]
    documents = (found.get("documents") or [[]])[0]

    hits = []

    for chunk_id, distance, meta, body in zip(
        ids, distances, metadatas, documents
    ):
        meta = meta or {}

        hits.append({
            "doc_id": meta.get("doc_id", ""),
            "period": str(meta.get("period") or ""),
            "page": int(meta.get("page") or 0),
            "chunk_id": chunk_id,
            "score": 1.0 - float(distance),
            "text": body or "",
        })

    hits.sort(key=lambda hit: hit["score"], reverse=True)

    return hits


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "--role":
        print('Usage: python src/retrieval/search.py --role <role> [--k N] "question"')
        sys.exit(1)
    role = sys.argv[2]
    args = sys.argv[3:]
    k = None
    if args and args[0] == "--k":
        k = int(args[1])
        args = args[2:]
    question = " ".join(args).strip()
    rows = search(question, role, k)
    print(json.dumps(rows, indent=2))
