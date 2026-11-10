
import sys
import json
import argparse
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.ingestion.ingest import ingest_all
from src.retrieval.vector_store import index_all_extracted
from src.retrieval.hybrid import search_hybrid
from src.answering.generator import generate_answer

def cmd_ingest(corpus_dir):
    corpus = Path(corpus_dir)

    if not corpus.is_dir():
        print(f"corpus directory not found: {corpus}", file=sys.stderr)
        return 1

    saved, skipped, failed = ingest_all(pdf_dir=corpus)
    print(
        f"extract: saved={saved} skipped={skipped} failed={failed}",
        flush=True,
    )

    if failed:
        return 1

    upserted, file_count, counts = index_all_extracted()

    print(
        "index: files={files} embedded={embedded} "
        "skip={skip} recorded={recorded} indexed={indexed} "
        "replaced={replaced}".format(
            files=file_count,
            embedded=upserted,
            **counts,
        ),
        flush=True,
    )

    print("ingest complete", flush=True)
    return 0



def cmd_search(role, k, question, rerank=False):
    try:
        results = search_hybrid(question, role, k, rerank=rerank)

        # CLI contract: return only required fields
        output = [
            {
                "doc_id": item["doc_id"],
                "page": item["page"],
                "chunk_id": item["chunk_id"],
                "score": item["score"],
                "text": item["text"],
            }
            for item in results
        ]

        print(json.dumps(output, indent=2, ensure_ascii=False))
        return 0

    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


def cmd_ask(role, question, rerank=False):
    try:
        results = search_hybrid(question, role, k=5, rerank=rerank)
        response = generate_answer(question, results)

        print(json.dumps(response, indent=2, ensure_ascii=False))
        return 0

    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest")
    ingest_parser.add_argument("corpus_dir")

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument(
        "--role",
        required=True,
        choices=["consumer", "health_industrials", "tech_telecom", "lead"],
    )
    search_parser.add_argument("--k", type=int, default=5)
    search_parser.add_argument("--rerank", action="store_true")
    search_parser.add_argument("question")
    ask_parser = subparsers.add_parser("ask")
    ask_parser.add_argument(
        "--role",
        required=True,
        choices=["consumer", "health_industrials", "tech_telecom", "lead"],
    )
    ask_parser.add_argument("--rerank", action="store_true")
    ask_parser.add_argument("question")

    args = parser.parse_args()

    if args.command == "ingest":
        return cmd_ingest(args.corpus_dir)

    if args.command == "search":
        return cmd_search(args.role, args.k, args.question, args.rerank)

    if args.command == "ask":
        return cmd_ask(args.role, args.question, args.rerank)

    return 1


if __name__ == "__main__":
    sys.exit(main())
