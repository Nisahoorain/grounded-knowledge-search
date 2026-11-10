# Phase 8 — retrieval and answer metrics
def evaluate_question(expected_doc_id, expected_pages, retrieved):
    expected_pages = set(int(p) for p in expected_pages)

    doc_rank = None
    page_rank = None

    for rank, hit in enumerate(retrieved, start=1):
        if hit["doc_id"] == expected_doc_id:
            if doc_rank is None:
                doc_rank = rank

            if int(hit["page"]) in expected_pages:
                if page_rank is None:
                    page_rank = rank

    return {
        "document_hit": doc_rank is not None,
        "page_hit": page_rank is not None,
        "document_rank": doc_rank,
        "page_rank": page_rank,
        "document_mrr": 1 / doc_rank if doc_rank else 0.0,
        "page_mrr": 1 / page_rank if page_rank else 0.0,
    }


def calculate_metrics(results):
    total = len(results)

    if total == 0:
        return {}

    document_hits = sum(r["document_hit"] for r in results)
    page_hits = sum(r["page_hit"] for r in results)

    return {
        "total_questions": total,
        "document_hit_at_k": document_hits,
        "document_hit_rate": round(document_hits / total, 4),
        "page_hit_at_k": page_hits,
        "page_hit_rate": round(page_hits / total, 4),
        "document_mrr": round(
            sum(r["document_mrr"] for r in results) / total, 4
        ),
        "page_mrr": round(
            sum(r["page_mrr"] for r in results) / total, 4
        ),
    }