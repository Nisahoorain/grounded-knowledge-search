import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.retrieval.hybrid import (
    blend_times,
    dedupe_pages,
    doc_quota,
    get_candidates,
    rerank_blend,
    rrf_fuse,
)
from src.retrieval.query_parse import parse_query

DATASET = PROJECT_DIR / "dev_questions.jsonl"
HOLDOUT = PROJECT_DIR / "questions.jsonl"

MODES = {
    "dense":         lambda d, b, q, k: d,
    "bm25":          lambda d, b, q, k: b,
    "hybrid-chunk":  lambda d, b, q, k: rrf_fuse(d, b, by_page=False),
    "chunk+dedupe1": lambda d, b, q, k: dedupe_pages(rrf_fuse(d, b, by_page=False), 1),
    "doc-quota":     lambda d, b, q, k: doc_quota(rrf_fuse(d, b, by_page=False), k),
    "dedupe+blend":  lambda d, b, q, k: rerank_blend(
        q, dedupe_pages(rrf_fuse(d, b, by_page=False), 1), top_n=20
    ),
}

# Production search_hybrid is chunk fusion, then dedupe. Misses follow that mode.
WATCH = "chunk+dedupe1"


def gold_ranks(results, gold_doc, gold_pages, k):
    out = []
    for rank, x in enumerate(results[:k], start=1):
        if (x["doc_id"] == gold_doc and x.get("page") is not None
                and int(x["page"]) in gold_pages):
            out.append((rank, int(x["page"])))
    return out


def load_questions(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def evaluate(k=10, candidate_k=50, use_filter=True, questions=None, label="dev"):
    if questions is None:
        questions = load_questions(DATASET)
    blend_times.clear()

    stats = {m: {"recall": [], "rr": [], "hit": []} for m in MODES}
    per_q = []

    for i, item in enumerate(questions, start=1):
        dense, bm25, _ = get_candidates(
            item["question"], item["role"], candidate_k, use_filter
        )
        gold_doc = item["doc_id"]
        gold_pages = set(map(int, item["pages"]))

        ranks = {}
        row = [item["id"], len(bm25)]
        lists = {}
        for name, fn in MODES.items():
            ranked = fn(dense, bm25, item["question"], k)
            lists[name] = ranked
            hits = gold_ranks(ranked, gold_doc, gold_pages, k)
            first = hits[0][0] if hits else None
            ranks[name] = first
            stats[name]["recall"].append(len({p for _, p in hits}) / len(gold_pages))
            stats[name]["rr"].append(1 / first if first else 0)
            stats[name]["hit"].append(first is not None)
            row.append(first)
        per_q.append(row)

        print(
            f"[{i}/{len(questions)}] {item['id']} bm25={len(bm25)} {WATCH}={ranks.get(WATCH)}",
            flush=True,
        )

        if WATCH in ranks and ranks[WATCH] is None:
            parsed = parse_query(item["question"])
            filt = [d["doc_id"] for d in parsed["docs"]]
            print(f"\n--- MISS {item['id']} {WATCH} ---")
            print("Q:", item["question"], "| role:", item["role"])
            print("Expected:", gold_doc, sorted(gold_pages))
            print(
                "Parsed companies:", sorted(parsed["companies"]),
                "years:", sorted(parsed["years"]),
                "types:", sorted(parsed.get("types") or []),
                "gold_in_filter:", gold_doc in filt,
                "docs:", filt,
            )
            print("ranks:", {m: ranks[m] for m in MODES})
            for name, res in (("DENSE", dense), ("BM25", bm25)):
                full = gold_ranks(res, gold_doc, gold_pages, candidate_k)
                print(f"{name} expected rank (top {candidate_k}):", full or "NOT FOUND")
            print("Top", WATCH, [(x["doc_id"], x["page"]) for x in lists[WATCH][:5]])
            for extra in ("doc-quota", "dedupe+blend"):
                if extra in lists:
                    print(
                        "Top", extra,
                        [(x["doc_id"], x["page"]) for x in lists[extra][:5]],
                        "rank", ranks[extra],
                    )

    n = len(questions)
    width = max(len(m) for m in MODES)
    print(f"\n===== {label} (n={n}, k={k}, filter={use_filter}) =====")
    print(f"{'mode':<{width}}{'Recall':>8}{'MRR':>8}{'Hit':>8}{'Fail':>6}")
    for name, s in stats.items():
        print(f"{name:<{width}}{sum(s['recall'])/n:>8.4f}{sum(s['rr'])/n:>8.4f}"
              f"{sum(s['hit'])/n:>8.4f}{n - sum(s['hit']):>6}")

    print("\nPer-question first rank (None = miss):")
    print(f"{'id':<12}{'#bm25':>6}", *[f"{m:>{width}}" for m in MODES])
    for r in per_q:
        print(f"{r[0]:<12}{r[1]:>6}", *[f"{str(x):>{width}}" for x in r[2:]])

    if blend_times:
        from src.retrieval.hybrid import rerank_load_s
        avg = sum(blend_times) / len(blend_times)
        print(f"\nBlend load: {rerank_load_s:.2f}s | "
              f"predict calls: {len(blend_times)} | "
              f"mean {avg*1000:.0f} ms | "
              f"max {max(blend_times)*1000:.0f} ms")


if __name__ == "__main__":
    evaluate(k=10, use_filter=True, label="dev dev_questions")
    holdout = [q for q in load_questions(HOLDOUT) if str(q["id"]).startswith("gks_")]
    evaluate(k=10, use_filter=True, questions=holdout, label="holdout gks_")
