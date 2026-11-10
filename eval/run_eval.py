# Phase 8 — evaluation runner
import argparse
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]

if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.retrieval.hybrid_search import search
from eval.metrics import evaluate_question, calculate_metrics


def load_questions(path):
    questions = []

    with open(path, encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue

            question = json.loads(line)

            required = ["id", "role", "question", "doc_id", "pages"]
            missing = [key for key in required if key not in question]

            if missing:
                raise ValueError(
                    f"Line {line_number}: missing fields {missing}"
                )

            questions.append(question)

    return questions


def run_evaluation(questions, k):
    results = []

    for number, item in enumerate(questions, start=1):
        print(
            f"[{number}/{len(questions)}] "
            f"{item['id']}: {item['question']}",
            flush=True,
        )

        retrieved = search(
            question=item["question"],
            role=item["role"],
            k=k,
        )

        metrics = evaluate_question(
            expected_doc_id=item["doc_id"],
            expected_pages=item["pages"],
            retrieved=retrieved,
        )

        results.append({
            "id": item["id"],
            "role": item["role"],
            "question": item["question"],
            "expected_doc_id": item["doc_id"],
            "expected_pages": item["pages"],
            "retrieved": [
                {
                    "doc_id": hit["doc_id"],
                    "page": hit["page"],
                    "chunk_id": hit["chunk_id"],
                    "score": hit["score"],
                }
                for hit in retrieved
            ],
            **metrics,
        })

        print(
            f"  Document: {'HIT' if metrics['document_hit'] else 'MISS'}"
            f" | Page: {'HIT' if metrics['page_hit'] else 'MISS'}",
            flush=True,
        )

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate hybrid retrieval on benchmark questions."
    )

    parser.add_argument(
        "--questions",
        default=str(PROJECT_DIR / "eval" / "questions.jsonl"),
    )

    parser.add_argument("--k", type=int, default=5)

    parser.add_argument(
        "--output",
        default=str(PROJECT_DIR / "eval" / "results.json"),
    )

    args = parser.parse_args()

    if args.k < 1:
        parser.error("--k must be at least 1")

    questions = load_questions(args.questions)

    if not questions:
        raise ValueError("No evaluation questions found.")

    results = run_evaluation(questions, args.k)
    summary = calculate_metrics(results)

    output = {
        "retrieval": "hybrid",
        "k": args.k,
        "summary": summary,
        "results": results,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(output, file, indent=2, ensure_ascii=False)

    print("\n========== EVALUATION SUMMARY ==========")
    print(f"Questions: {summary['total_questions']}")
    print(
        f"Document Hit@{args.k}: "
        f"{summary['document_hit_at_k']}/{summary['total_questions']} "
        f"({summary['document_hit_rate'] * 100:.1f}%)"
    )
    print(
        f"Page Hit@{args.k}: "
        f"{summary['page_hit_at_k']}/{summary['total_questions']} "
        f"({summary['page_hit_rate'] * 100:.1f}%)"
    )
    print(f"Document MRR: {summary['document_mrr']}")
    print(f"Page MRR: {summary['page_mrr']}")
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    main()