"""Run grounded answers for the benchmark questions."""

import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.generation.answer import answer


def main():
    questions_path = PROJECT_DIR / "eval" / "questions.jsonl"
    output_path = PROJECT_DIR / "eval" / "answers.json"
    questions = [
        json.loads(line)
        for line in questions_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    saved = []
    if output_path.exists():
        saved = json.loads(output_path.read_text(encoding="utf-8"))
    done = {item["id"] for item in saved if item.get("status")}
    results = [item for item in saved if item.get("status")]
    for number, item in enumerate(questions, start=1):
        if item["id"] in done:
            print(f"[{number}/{len(questions)}] {item['id']} skipped", flush=True)
            continue
        print(f"[{number}/{len(questions)}] {item['id']}", flush=True)
        try:
            result = answer(item["question"], item["role"])
        except Exception as exc:
            result = {
                "status": "refused",
                "answer": str(exc),
                "citations": [],
                "error": True,
            }
        results.append({
            "id": item["id"],
            "role": item["role"],
            "question": item["question"],
            "expected_doc_id": item["doc_id"],
            "expected_pages": item["pages"],
            **result,
        })
        output_path.write_text(
            json.dumps(results, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(
            f"  status={result.get('status')} answer={result.get('answer')}",
            flush=True,
        )
    print(f"\nSaved {len(results)} answers to {output_path}", flush=True)


if __name__ == "__main__":
    main()
