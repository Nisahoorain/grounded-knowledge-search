import time

from src.retrieval.vector_store import search_chunks
from src.retrieval.fts_store import search_bm25, ROLE_DESKS
from src.retrieval.query_parse import parse_query

_ce = None
rerank_load_s = None
rerank_times = []
blend_times = []


def _cross_encoder(max_length):
    global _ce, rerank_load_s
    if _ce is None:
        from sentence_transformers import CrossEncoder

        t0 = time.perf_counter()
        _ce = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2", max_length=max_length)
        rerank_load_s = time.perf_counter() - t0
    _ce.max_length = max_length
    return _ce


def dedupe_pages(ranked, max_per_page=1):
    out, counts = [], {}
    for x in ranked:
        key = (x["doc_id"], x["page"])
        if counts.get(key, 0) >= max_per_page:
            continue
        out.append(x)
        counts[key] = counts.get(key, 0) + 1
    return out


def get_candidates(question, role, candidate_k=50, use_filter=True):
    """Returns (dense, bm25, blocked). blocked=True => company role ko allowed nahi."""
    dense_q, bm25_q, doc_ids = question, question, None

    if use_filter:
        p = parse_query(question)
        if p["companies"]:
            allowed = set(ROLE_DESKS[role])
            doc_ids = [d["doc_id"] for d in p["docs"] if d["desk"] in allowed]
            if not doc_ids:
                return [], [], True
        if p["clean_question"]:
            bm25_q = p["clean_question"]

    dense = search_chunks(dense_q, role, candidate_k, doc_ids=doc_ids)
    bm25 = search_bm25(bm25_q, role, candidate_k, doc_ids=doc_ids)
    return dense, bm25, False


def rrf_fuse(dense, bm25, rrf_constant=60, dense_weight=1.0, bm25_weight=1.0,
             by_page=False):
    """RRF at chunk level. by_page=True merges a page's chunks before scoring."""
    fused = {}
    for source, weight, results in (
        ("dense", dense_weight, dense),
        ("bm25", bm25_weight, bm25),
    ):
        for rank, item in enumerate(results, start=1):
            key = (item["doc_id"], item["page"]) if by_page else item["chunk_id"]
            if key not in fused:
                fused[key] = {**item, "score": 0.0, "dense_rank": None, "bm25_rank": None}
            # ek source se ek page par sirf sab se behtar rank count ho
            if fused[key][f"{source}_rank"] is None:
                fused[key]["score"] += weight / (rrf_constant + rank)
                fused[key][f"{source}_rank"] = rank
    return sorted(fused.values(), key=lambda x: x["score"], reverse=True)


def rerank(question, items, top_n=30):
    """Cross-encoder scores alone. Returns the reranked pool, then the untouched tail."""
    pool = items[:top_n]
    if not pool:
        return items
    ce = _cross_encoder(512)
    t1 = time.perf_counter()
    scores = ce.predict([(question, x["text"]) for x in pool])
    rerank_times.append(time.perf_counter() - t1)
    for x, s in zip(pool, scores):
        x["rerank"] = float(s)
    return sorted(pool, key=lambda x: x["rerank"], reverse=True) + items[top_n:]


def rerank_blend(question, ranked, top_n=20, c=60):
    """Fusion rank plus cross-encoder rank. Text is truncated and max_length is 256."""
    pool = ranked[:top_n]
    if not pool:
        return ranked
    ce = _cross_encoder(256)
    t1 = time.perf_counter()
    scores = ce.predict(
        [(question, x["text"][:1200]) for x in pool],
        batch_size=16,
    )
    blend_times.append(time.perf_counter() - t1)
    order = sorted(range(len(pool)), key=lambda i: scores[i], reverse=True)
    rr_rank = {i: r for r, i in enumerate(order, start=1)}
    for i, x in enumerate(pool):
        x["score"] = 1 / (c + (i + 1)) + 1 / (c + rr_rank[i])
    return sorted(pool, key=lambda x: x["score"], reverse=True) + ranked[top_n:]


def doc_quota(ranked, k, per_doc=2, max_docs=4):
    """Keep at least per_doc chunks from each doc when a few docs share the list."""
    docs = list(dict.fromkeys(x["doc_id"] for x in ranked))
    if not (1 < len(docs) <= max_docs):
        return ranked[:k]
    reserved, counts = [], {}
    for x in ranked:
        if counts.get(x["doc_id"], 0) < per_doc:
            reserved.append(x)
            counts[x["doc_id"]] = counts.get(x["doc_id"], 0) + 1
    rest = [x for x in ranked if x not in reserved]
    return (reserved + rest)[:k]


def search_hybrid(question, role, k=5, candidate_k=50, rrf_constant=60,
                  bm25_weight=1.0, page_dedupe=1, use_filter=True, rerank=False):
    dense, bm25, blocked = get_candidates(question, role, candidate_k, use_filter)
    if blocked:
        return []
    ranked = rrf_fuse(
        dense, bm25, rrf_constant, bm25_weight=bm25_weight, by_page=False
    )
    if page_dedupe:
        ranked = dedupe_pages(ranked, page_dedupe)
    if rerank:
        ranked = rerank_blend(question, ranked, top_n=20)
    return ranked[:k]
