"""Answer-quality eval. Put at eval/evaluate_answers.py.

Runs the real `ask` path (retrieve_for_ask + generate_answer) on every
question and reports:

  ask_retrieval_hit  gold page is among the chunks `ask` actually receives
                     (includes statement rows, unlike evaluate_retrieval.py)
  cited_gold         a returned citation is the gold doc + a gold page
  correct            auto-scored answer match (see below), None = needs a human
  refusal_ok         for questions marked "answerable": false -> status refused
  latency            wall time per question, against the 5 s budget

API failures are recorded as status=error (not refused). Provider attempts
(primary + fallback) are stored on each row under `llm`.

Structured scoring (preferred):
  - yes_no_numeric: polarity AND expected_number AND metric keywords
  - numeric: expected_number (with unit-scale tolerance)
  - yes_no: polarity only
  - narrative / missing answer_type: correct=None (manual review)

`verified`, `correct`, and `ask_retrieval_hit` stay independent metrics.

Usage:
  python eval/evaluate_answers.py [--rerank] [--limit N] [--delay SECONDS]
                                  [--tag TAG]
  python eval/evaluate_answers.py --rescore eval/answer_results_gemini_delay8_dev.jsonl
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
from src.config import ANSWER_PROVIDER, LLM_REQUEST_DELAY_S
from src.retrieval.pipeline import retrieve_for_ask, to_contract

DEV = PROJECT_DIR / "dev_questions.jsonl"
HOLDOUT = PROJECT_DIR / "questions.jsonl"
OUT_DIR = PROJECT_DIR / "eval"
BUDGET_S = 5.0

NUM = re.compile(r"-?\d[\d,]*\.?\d*")
STRUCTURED_FIELDS = (
    "answer_type",
    "expected_polarity",
    "expected_number",
    "expected_unit",
    "expected_metric",
    "expected_metric_keywords",
)


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


def _polarity_ok(expected, answer):
    if not expected:
        return False
    return bool(re.search(rf"\b{re.escape(expected.lower())}\b", (answer or "").lower()[:80]))


def _number_ok(expected, answer):
    if expected is None:
        return False
    target = float(expected)
    for x in nums(answer):
        for scale in (1, 1000, 0.001):
            if close(x * scale, target):
                return True
    return False


def _metric_ok(question, answer):
    keywords = question.get("expected_metric_keywords")
    if not keywords:
        metric = (question.get("expected_metric") or "").strip().lower()
        keywords = [metric] if metric else []
    text = (answer or "").lower()
    return bool(keywords) and all(str(k).lower() in text for k in keywords)


def score_answer(question, answer, status="answered"):
    """True / False / None(manual). Uses structured fields when present.

    Blind free-text Yes/No scoring is disabled: missing answer_type => None
    when the system answered, False when it refused/errored.
    """
    if status != "answered":
        return False

    answer_type = (question.get("answer_type") or "").strip().lower()
    if not answer_type:
        return None
    if answer_type == "narrative":
        return None
    if answer_type == "yes_no":
        return _polarity_ok(question.get("expected_polarity"), answer)
    if answer_type == "numeric":
        if question.get("expected_number") is None:
            return None
        return _number_ok(question.get("expected_number"), answer)
    if answer_type == "yes_no_numeric":
        if question.get("expected_number") is None or not question.get("expected_polarity"):
            return None
        return (
            _polarity_ok(question.get("expected_polarity"), answer)
            and _number_ok(question.get("expected_number"), answer)
            and _metric_ok(question, answer)
        )
    return None


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


def run(questions, rerank, delay_s):
    rows = []
    for i, q in enumerate(questions, start=1):
        if i > 1 and delay_s > 0:
            print(f"delay {delay_s:.1f}s before next LLM call...", flush=True)
            time.sleep(delay_s)

        gold_pages = set(map(int, q.get("pages", [])))
        answerable = q.get("answerable", True)
        t0 = time.perf_counter()
        error, chunks, response = None, [], {"status": "error", "citations": []}
        try:
            chunks = retrieve_for_ask(q["question"], q["role"], k=5, rerank=rerank)
            response = to_contract(generate_answer(q["question"], chunks))
        except Exception as exc:
            # Keep API / provider failures distinct from grounded refusals.
            error = str(exc)
            response = {
                "status": "error",
                "answer": None,
                "citations": [],
                "verified": None,
            }
        elapsed = time.perf_counter() - t0

        llm = response.get("llm") or {}
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
            "provider_used": llm.get("provider_used"),
            "fallback_used": llm.get("fallback_used"),
            "llm_attempts": llm.get("attempts"),
        }
        for key in STRUCTURED_FIELDS:
            if key in q:
                row[key] = q[key]
        if answerable:
            row["ask_retrieval_hit"] = retrieval_hit(chunks, q["doc_id"], gold_pages)
            row["cited_gold"] = cited_gold(response, q["doc_id"], gold_pages)
            row["correct"] = score_answer(
                q, response.get("answer"), status=response.get("status")
            )
        else:
            row["refusal_ok"] = response.get("status") == "refused"
        rows.append(row)
        print(
            f"[{i}/{len(questions)}] {q['id']} status={row['status']} "
            f"provider={row.get('provider_used')} hit={row.get('ask_retrieval_hit')} "
            f"cited={row.get('cited_gold')} correct={row.get('correct')} "
            f"{elapsed:.1f}s",
            flush=True,
        )
    return rows


def pct(num, den):
    return f"{num}/{den} = {num / den:.0%}" if den else "n/a"


def summarize(rows, label):
    ans = [r for r in rows if r["answerable"]]
    unans = [r for r in rows if not r["answerable"]]
    scored = [r for r in ans if r.get("correct") is not None]
    times = sorted(r["seconds"] for r in rows)
    print(f"\n===== {label} (n={len(rows)}) =====")
    print("ask-path retrieval hit :", pct(sum(r.get("ask_retrieval_hit") for r in ans), len(ans)))
    print("cited gold doc+page    :", pct(sum(r.get("cited_gold") for r in ans), len(ans)))
    print("answered (not refused) :", pct(sum(r["status"] == "answered" for r in ans), len(ans)))
    print("api/provider errors    :", pct(sum(r["status"] == "error" for r in rows), len(rows)))
    print("correct (auto-scored)  :", pct(sum(bool(r.get("correct")) for r in scored), len(scored)),
          f"| {len(ans) - len(scored)} need manual review")
    print("answered but unverified:", sum(r["status"] == "answered" and r.get("verified") is False
                                          for r in rows))
    if unans:
        print("refused unanswerable   :", pct(sum(r.get("refusal_ok") for r in unans), len(unans)))
    else:
        print("refused unanswerable   : NOT MEASURED (no questions with answerable=false)")
    if times:
        p90 = times[min(len(times) - 1, int(0.9 * len(times)))]
        over = sum(t > BUDGET_S for t in times)
        print(f"latency                : median {statistics.median(times):.1f}s | "
              f"p90 {p90:.1f}s | max {times[-1]:.1f}s | over {BUDGET_S:.0f}s budget: {over}/{len(times)}")
    errors = [r for r in rows if r["error"] or r["status"] == "error"]
    if errors:
        first = errors[0].get("error") or "status=error"
        print("errors                 :", len(errors), "(first:", first[:100], ")")


def dataset_by_id():
    by_id = {}
    for path in (DEV, HOLDOUT):
        for q in load(path):
            by_id[q["id"]] = q
    return by_id


def rescore_rows(rows, by_id):
    """Offline: keep answers/latency; recompute correct from structured gold."""
    out = []
    for row in rows:
        q = dict(by_id.get(row["id"], {}))
        # Prefer dataset gold; fall back to fields already on the result row.
        for key in STRUCTURED_FIELDS:
            if key in row and key not in q:
                q[key] = row[key]
        if "answer" not in q and row.get("gold") is not None:
            q["answer"] = row["gold"]
        new_row = dict(row)
        for key in STRUCTURED_FIELDS:
            if key in q:
                new_row[key] = q[key]
        if new_row.get("answerable", True):
            old = new_row.get("correct")
            new_row["correct_old"] = old
            new_row["correct"] = score_answer(
                q, new_row.get("answer"), status=new_row.get("status")
            )
        out.append(new_row)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rerank", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--delay",
        type=float,
        default=LLM_REQUEST_DELAY_S,
        help="Seconds to wait between questions (rate-limit control)",
    )
    ap.add_argument(
        "--tag",
        default="",
        help="Optional file tag, e.g. groq / gemini / controlled",
    )
    ap.add_argument(
        "--rescore",
        default="",
        help="Offline re-score an existing answer_results*.jsonl (no API calls)",
    )
    args = ap.parse_args()

    if args.rescore:
        src = Path(args.rescore)
        if not src.is_file():
            src = OUT_DIR / args.rescore
        rows = load(src)
        rescored = rescore_rows(rows, dataset_by_id())
        out = src.with_name(src.stem + "_rescored.jsonl")
        out.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rescored),
            encoding="utf-8",
        )
        changed = [
            r for r in rescored
            if r.get("correct_old") != r.get("correct")
        ]
        print(f"rescored {len(rescored)} rows from {src}")
        print(f"wrote {out}")
        print(f"correct changed: {len(changed)}")
        for r in changed:
            print(
                f"  {r['id']}: {r.get('correct_old')} -> {r.get('correct')} "
                f"(type={r.get('answer_type')})"
            )
        for label in ("dev", "holdout"):
            subset = [r for r in rescored if r.get("set") == label]
            if subset:
                summarize(subset, f"{label} rescored")
        summarize(rescored, "all rescored")
        return

    tag = (args.tag or ANSWER_PROVIDER or "run").strip().lower()
    out_tagged = OUT_DIR / f"answer_results_{tag}.jsonl"
    out_latest = OUT_DIR / "answer_results.jsonl"

    print(
        f"provider={ANSWER_PROVIDER} delay={args.delay}s "
        f"tag={tag} out={out_tagged.name}",
        flush=True,
    )

    # Preserve prior provider-specific files: never overwrite another tag.
    if out_tagged.exists():
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup = OUT_DIR / f"answer_results_{tag}_{stamp}.jsonl"
        out_tagged.replace(backup)
        print(f"preserved previous {out_tagged.name} -> {backup.name}", flush=True)

    # Also keep the previous latest pointer if it exists.
    if out_latest.exists():
        stamp = time.strftime("%Y%m%d_%H%M%S")
        latest_backup = OUT_DIR / f"answer_results_latest_{stamp}.jsonl"
        out_latest.replace(latest_backup)
        print(f"preserved previous answer_results.jsonl -> {latest_backup.name}", flush=True)

    dev = [dict(q, _set="dev") for q in load(DEV)]
    hold = [dict(q, _set="holdout") for q in load(HOLDOUT)
            if str(q["id"]).startswith("gks_")]
    questions = dev + hold
    if args.limit:
        questions = questions[:args.limit]

    rows = run(questions, args.rerank, args.delay)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    out_tagged.write_text(payload, encoding="utf-8")
    out_latest.write_text(payload, encoding="utf-8")

    for label in ("dev", "holdout"):
        subset = [r for r in rows if r["set"] == label]
        if subset:
            summarize(subset, label)
    summarize(rows, "all")
    print(f"\nper-question results: {out_tagged}")
    print(f"latest pointer:       {out_latest}")


if __name__ == "__main__":
    main()

