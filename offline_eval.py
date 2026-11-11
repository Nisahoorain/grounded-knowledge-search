"""Zero-API pass over the eval set. Run from the project root:

    python offline_eval.py eval.jsonl
    python offline_eval.py eval.jsonl --ids fb_00941,gks_001

The LLM call is replaced by a stub that raises, so no request leaves your
machine. Questions that generate_answer() settles without the model
(answer_from_formula / answer_from_reported_line) are scored for real.
Everything else is reported as needs_llm.

Only rows that are needs_llm AND gold_in_ctx are worth spending API calls on.
"""
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


class NeedsLLM(Exception):
    pass


def _no_llm(prompt):
    raise NeedsLLM()


def has_gold(items, gold_doc, gold_pages):
    return any(
        i.get("doc_id") == gold_doc and i.get("page") in gold_pages
        for i in items
    )


def main(path, only=None):
    from src.answering import generator
    from src.retrieval.pipeline import retrieve_for_ask

    generator.chat_complete_with_meta = _no_llm

    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    if only:
        rows = [r for r in rows if r["id"] in only]

    print("id | outcome | gold_in_ctx | cited_gold | cited pages", flush=True)
    spend = []
    free_hits = free_total = 0
    for row in rows:
        gold_doc, gold_pages = row["doc_id"], set(row["pages"])
        results = retrieve_for_ask(row["question"], row["role"], k=5)
        in_ctx = has_gold(results, gold_doc, gold_pages)

        cited = None
        pages = ""
        try:
            resp = generator.generate_answer(row["question"], results)
        except NeedsLLM:
            outcome = "needs_llm"
            if in_ctx:
                spend.append(row["id"])
        else:
            cits = resp.get("citations") or []
            outcome = resp.get("status", "?")
            cited = has_gold(cits, gold_doc, gold_pages)
            pages = ",".join(f"{c['doc_id']}:p{c['page']}" for c in cits)
            free_total += 1
            free_hits += bool(cited)

        print(
            " | ".join([
                row["id"],
                outcome,
                str(in_ctx),
                "-" if cited is None else str(cited),
                pages,
            ]),
            flush=True,
        )

    print(
        f"\nanswered without the LLM: {free_total}, of which cited gold: {free_hits}",
        flush=True,
    )
    print(
        f"worth an API call (needs_llm and gold in context): {len(spend)}",
        flush=True,
    )
    if spend:
        print("  --ids " + ",".join(spend), flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    ids = None
    if "--ids" in sys.argv:
        ids = set(sys.argv[sys.argv.index("--ids") + 1].split(","))
    main(sys.argv[1], ids)