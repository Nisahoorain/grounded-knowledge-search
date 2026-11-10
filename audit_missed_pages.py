
import json
from pathlib import Path

from src.retrieval import hybrid_search as hs
from src.access_control import desks_for


QUESTIONS_FILE = Path("eval/questions.jsonl")
OUTPUT_FILE = Path("eval/page_aggregation_diagnostic.json")

K = 5


def get_questions():
    questions = []

    with QUESTIONS_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                questions.append(json.loads(line))

    return questions


def aggregate_pages(chunks):
    pages = {}

    for hit in chunks:
        doc_id = hit.get("doc_id")
        page = hit.get("page")

        if doc_id is None or page is None:
            continue

        key = (doc_id, page)
        score = float(hit.get("score", 0))

        if key not in pages or score > pages[key]["score"]:
            pages[key] = {
                "doc_id": doc_id,
                "page": page,
                "score": score,
                "chunk_id": hit.get("chunk_id"),
                "text": hit.get("text", "")[:250],
            }

    return sorted(
        pages.values(),
        key=lambda x: x["score"],
        reverse=True,
    )


def inspect_source(name, hits):
    target_doc = "PEPSICO_2022_10K"
    target_page = 26

    matches = [
        h for h in hits
        if h.get("doc_id") == target_doc
        and h.get("page") == target_page
    ]

    print(f"\n{name}:")
    print("TOTAL CANDIDATES:", len(hits))
    print("EXPECTED PAGE MATCHES:", len(matches))

    for hit in matches:
        print({
            "chunk_id": hit.get("chunk_id"),
            "score": hit.get("score"),
            "text": hit.get("text", "")[:500],
        })


def main():
    questions = get_questions()
    results = []

    for index, item in enumerate(questions, start=1):
        question = item["question"]
        role = item["role"]

        print(f"\n[{index}/{len(questions)}] {item.get('id', '')}")

        text = question.strip()
        limit = K
        pool = max(limit, hs.CANDIDATE_POOL)
        desks = desks_for(role)
        company_docs = hs.named_company(text)

        years = {
            str(year) for year in hs._requested_years(text)
        }

        year_lock = (
            set() if hs._forward_looking(text) else years
        )

        kind = hs._evidence_kind(text)

        semantic_hits = hs.semantic_search(
            text,
            role,
            pool * (5 if year_lock else 2),
            company_docs=company_docs,
            periods=year_lock or None,
        )

        semantic_hits = hs._prefer_kind(
            hs._drop_contents(
                hs._matching_period(semantic_hits, year_lock)
            ),
            kind,
        )[:pool]

        text_hits = hs._bm25(
            text,
            desks,
            pool,
            company_docs,
            content_type="text",
            periods=year_lock or None,
        )

        table_hits = hs._bm25(
            text,
            desks,
            pool,
            company_docs,
            content_type="table",
            periods=year_lock or None,
        )

        # Inspect each retrieval source for PepsiCo only.
        if item.get("id") == "fb_00735":
            inspect_source("SEMANTIC", semantic_hits)
            inspect_source("BM25 TEXT", text_hits)
            inspect_source("BM25 TABLE", table_hits)

        hit_lists = [semantic_hits]

        if kind != "table":
            hit_lists.append(text_hits)

        if kind != "text":
            hit_lists.append(table_hits)

        statement_hits = hs._statement_hits(
            text,
            [hit for group in hit_lists for hit in group],
            pool,
        )

        if item.get("id") == "fb_00735":
            inspect_source("STATEMENT HITS", statement_hits)

        hit_lists.append(statement_hits)

        chunk_hits = hs._fuse(
            hit_lists,
            pool,
            requested_years=year_lock or None,
            prefer_table=bool(kind == "table"),
        )

        if item.get("id") == "fb_00735":
            inspect_source("FUSED CANDIDATES", chunk_hits)

        page_hits = aggregate_pages(chunk_hits)

        results.append({
            "id": item.get("id"),
            "question": question,
            "role": role,
            "expected_doc_id": item["doc_id"],
            "expected_pages": item["pages"],
            "chunk_top5": [
                {
                    "doc_id": h.get("doc_id"),
                    "page": h.get("page"),
                    "score": h.get("score"),
                    "chunk_id": h.get("chunk_id"),
                }
                for h in chunk_hits[:K]
            ],
            "page_top5": page_hits[:K],
        })

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("\nDiagnostic completed.")
    print(f"Questions: {len(results)}")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
