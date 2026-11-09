"""Score the 20 dev questions against the current index. Read-only.

A document hit means the expected doc_id appears in the top k.
A page hit means one of those hits is that document on an expected page.
Questions with several gold pages count as a page hit when any one of them
is retrieved. Nothing here writes to Chroma or changes embeddings.
"""

import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.retrieval.search import search

QUESTIONS_PATH = PROJECT_DIR / "dev_questions.jsonl"
K = 5


def load_questions(path):
    rows = []
    with open(path, encoding="utf-8") as file:
        for line_no, line in enumerate(file, start=1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            missing = [key for key in ("id", "role", "question", "doc_id", "pages") if key not in row]
            if missing:
                raise ValueError(f"{path.name}:{line_no} missing {', '.join(missing)}")
            pages = [int(page) for page in row["pages"]]
            if not pages:
                raise ValueError(f"{path.name}:{line_no} has no pages")
            rows.append(
                {
                    "id": row["id"],
                    "role": row["role"],
                    "question": row["question"],
                    "doc_id": row["doc_id"],
                    "pages": pages,
                }
            )
    return rows


def grade(question, hits):
    expected_pages = set(question["pages"])
    doc_pages = sorted(
        {
            hit["page"]
            for hit in hits
            if hit["doc_id"] == question["doc_id"] and hit["page"] in expected_pages
        }
    )
    doc_hit = any(hit["doc_id"] == question["doc_id"] for hit in hits)
    return {"doc_hit": doc_hit, "page_hit": bool(doc_pages), "matched_pages": doc_pages}


def evaluate(path=QUESTIONS_PATH, k=K, search_fn=search, show=False):
    questions = load_questions(path)
    graded = []
    for question in questions:
        hits = search_fn(question["question"], question["role"], k)
        if show:
            print(f"\n{question['id']}  {question['role']}")
            print(question["question"])
            print(json.dumps(hits, indent=2))
        result = grade(question, hits)
        result["id"] = question["id"]
        result["doc_id"] = question["doc_id"]
        result["pages"] = question["pages"]
        graded.append(result)
    return graded


def report(graded, k=K, retriever="semantic"):
    total = len(graded)
    doc_hits = sum(result["doc_hit"] for result in graded)
    page_hits = sum(result["page_hit"] for result in graded)
    doc_misses = [result["id"] for result in graded if not result["doc_hit"]]
    page_misses = [result["id"] for result in graded if not result["page_hit"]]

    lines = [f"retriever: {retriever}", f"questions: {total}", f"k: {k}", ""]
    for result in graded:
        doc_mark = "HIT" if result["doc_hit"] else "MISS"
        page_mark = "HIT" if result["page_hit"] else "MISS"
        found = ",".join(str(page) for page in result["matched_pages"]) or "-"
        expected = ",".join(str(page) for page in result["pages"])
        lines.append(
            f"{result['id']}  doc {doc_mark}  page {page_mark}  "
            f"{result['doc_id']} expected [{expected}] matched [{found}]"
        )
    lines.extend(
        [
            "",
            f"Document Hit@{k}: {doc_hits}/{total} ({100.0 * doc_hits / total:.1f}%)",
            f"Page Hit@{k}: {page_hits}/{total} ({100.0 * page_hits / total:.1f}%)",
            "",
            "Document misses: " + (", ".join(doc_misses) if doc_misses else "none"),
            "Page misses: " + (", ".join(page_misses) if page_misses else "none"),
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    show = "--show" in sys.argv
    if "--hybrid" in sys.argv:
        from src.retrieval.hybrid_search import search as hybrid_search

        graded = evaluate(search_fn=hybrid_search, show=show)
        print(report(graded, retriever="hybrid"))
    else:
        print(report(evaluate(show=show)))
