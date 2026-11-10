"""Answer-quality eval. Put at eval/evaluate_answers.py.

Runs the real `ask` path (retrieve_for_ask + generate_answer) on every
question and reports:

  ask_retrieval_hit  gold page is among the chunks `ask` actually receives
                     (includes statement rows, unlike evaluate_retrieval.py)
  cited_gold         a returned citation is the gold doc + a gold page
  correct            auto-scored answer match (see below), None = needs a human
  refusal_ok         for questions marked "answerable": false -> status refused
  latency            wall time per question, against the 5 s budget

Auto scoring is deliberately simple:
  - gold starts with Yes/No -> polarity must match in the answer's first 80 chars
  - else gold has a number  -> some number in the answer is within 0.5% of the
                               first gold number (also accepts x1000 / x0.001
                               for thousands/millions/billions unit slips)
  - else                    -> not auto-scored; listed for manual review
Auto scoring can be wrong either way. Read answer_results.jsonl for the misses.

Usage:  python eval/evaluate_answers.py [--rerank] [--limit N]
"""
import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.answering.generator import generate_answer
from src.retrieval.pipeline import retrieve_for_ask, to_contract

DEV = PROJECT_DIR / "dev_questions.jsonl"
HOLDOUT = PROJECT_DIR / "questions.jsonl"
OUT = PROJECT_DIR / "eval" / "answer_results.jsonl"
BUDGET_S = 5.0

NUM = re.compile(r"-?\d[\d,]*\.?\d*")


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def nums(text):
    out = []
    for m in NUM.findall(text or ""):
        try:
            out.append(float(m.replace(",", "").rstrip(".")))
        except ValueError:
            pass
    return out


def close(a, b, tol=0.005):
    return abs(a - b) <= tol * max(abs(b), 1e-9)


def score_answer(gold, answer):
    """True / False, or None when it cannot be auto-scored."""
    if not gold:
        return None
    first = re.match(r"\s*(yes|no)\b", gold.lower())
    if first:
        return bool(re.search(rf"\b{first.group(1)}\b", (answer or "").lower()[:80]))
    g = nums(gold)
    if not g:
        return None
    for x in nums(answer):
        for scale in (1, 1000, 0.001):
            if close(x * scale, g[0]):
                return True
    return False


def cited_gold(response, gold_doc, gold_pages):
    return any(
        c.get("doc_id") == gold_doc and int(c.get("page", -1)) in gold_pages
        for c in response.get("citations", [])
    )


def retrieval_hit(chunks, gold_doc, gold_pages):
    return any(
        c.get("doc_id") == gold_doc and c.get("page") is not None
        and int(c["page"]) in gold_pages
        for c in chunks
    )


def run(questions, rerank):
    rows = []
    for i, q in enumerate(questions, start=1):
        gold_pages = set(map(int, q.get("pages", [])))
        answerable = q.get("answerable", True)
        t0 = time.perf_counter()
        error, chunks, response = None, [], {"status": "refused", "citations": []}
        try:
            chunks = retrieve_for_ask(q["question"], q["role"], k=5, rerank=rerank)
            response = to_contract(generate_answer(q["question"], chunks))
        except Exception as exc:  # e.g. Ollama not running
            error = str(exc)
        elapsed = time.perf_counter() - t0

        row = {
            "id": q["id"],
            "set": q["_set"],
            "answerable": answerable,
            "status": response.get("status"),
            "verified": response.get("verified"),
            "seconds": round(elapsed, 2),
            "error": error,
            "answer": response.get("answer"),
            "gold": q.get("answer"),
        }
        if answerable:
            row["ask_retrieval_hit"] = retrieval_hit(chunks, q["doc_id"], gold_pages)
            row["cited_gold"] = cited_gold(response, q["doc_id"], gold_pages)
            row["correct"] = (
                score_answer(q.get("answer"), response.get("answer"))
                if response.get("status") == "answered" else False
            )
        else:
            row["refusal_ok"] = response.get("status") == "refused"
        rows.append(row)
        print(
            f"[{i}/{len(questions)}] {q['id']} status={row['status']} "
            f"hit={row.get('ask_retrieval_hit')} cited={row.get('cited_gold')} "
            f"correct={row.get('correct')} {elapsed:.1f}s",
            flush=True,
        )
    return rows


def pct(num, den):
    return f"{num}/{den} = {num / den:.0%}" if den else "n/a"


def summarize(rows, label):
    ans = [r for r in rows if r["answerable"]]
    unans = [r for r in rows if not r["answerable"]]
    scored = [r for r in ans if r["correct"] is not None]
    times = sorted(r["seconds"] for r in rows)
    print(f"\n===== {label} (n={len(rows)}) =====")
    print("ask-path retrieval hit :", pct(sum(r["ask_retrieval_hit"] for r in ans), len(ans)))
    print("cited gold doc+page    :", pct(sum(r["cited_gold"] for r in ans), len(ans)))
    print("answered (not refused) :", pct(sum(r["status"] == "answered" for r in ans), len(ans)))
    print("correct (auto-scored)  :", pct(sum(bool(r["correct"]) for r in scored), len(scored)),
          f"| {len(ans) - len(scored)} need manual review")
    print("answered but unverified:", sum(r["status"] == "answered" and r["verified"] is False
                                          for r in rows))
    if unans:
        print("refused unanswerable   :", pct(sum(r["refusal_ok"] for r in unans), len(unans)))
    else:
        print("refused unanswerable   : NOT MEASURED (no questions with answerable=false)")
    if times:
        p90 = times[min(len(times) - 1, int(0.9 * len(times)))]
        over = sum(t > BUDGET_S for t in times)
        print(f"latency                : median {statistics.median(times):.1f}s | "
              f"p90 {p90:.1f}s | max {times[-1]:.1f}s | over {BUDGET_S:.0f}s budget: {over}/{len(times)}")
    errors = [r for r in rows if r["error"]]
    if errors:
        print("errors                 :", len(errors), "(first:", errors[0]["error"][:100], ")")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rerank", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    dev = [dict(q, _set="dev") for q in load(DEV)]
    hold = [dict(q, _set="holdout") for q in load(HOLDOUT)
            if str(q["id"]).startswith("gks_")]
    questions = dev + hold
    if args.limit:
        questions = questions[:args.limit]

    rows = run(questions, args.rerank)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    for label in ("dev", "holdout"):
        subset = [r for r in rows if r["set"] == label]
        if subset:
            summarize(subset, label)
    summarize(rows, "all")
    print(f"\nper-question results: {OUT}")


if __name__ == "__main__":
    main()