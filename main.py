"""Command-line interface for grounded knowledge search.

    python main.py ingest <corpus_dir>
    python main.py search --role <role> --k <n> "<question>"
    python main.py ask --role <role> "<question>"
"""

import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.generation.answer import answer
from src.ingestion.ingest import ingest_all
from src.retrieval.hybrid_search import build_index, get_index, search
from src.retrieval.vector_store import index_all_extracted


def _search_row(hit):
    return {
        "doc_id": hit["doc_id"],
        "page": int(hit["page"]),
        "chunk_id": hit["chunk_id"],
        "score": hit["score"],
        "text": hit.get("text") or "",
    }


def cmd_ingest(corpus_dir):
    corpus = Path(corpus_dir)
    if not corpus.is_dir():
        print(f"corpus directory not found: {corpus}", file=sys.stderr)
        return 1
    saved, skipped, failed = ingest_all(pdf_dir=corpus)
    print(f"extract: saved={saved} skipped={skipped} failed={failed}", flush=True)
    if failed:
        return 1
    upserted, file_count, counts = index_all_extracted()
    print(
        "index: files={files} embedded={embedded} "
        "skip={skip} recorded={recorded} indexed={indexed} replaced={replaced}".format(
            files=file_count,
            embedded=upserted,
            **counts,
        ),
        flush=True,
    )
    if counts["indexed"] or counts["replaced"]:
        build_index()
    else:
        get_index()
    print("ingest complete", flush=True)
    return 0


def cmd_search(role, k, question):
    rows = [_search_row(hit) for hit in search(question, role, k)]
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return 0


def cmd_ask(role, question):
    print(json.dumps(answer(question, role), indent=2, ensure_ascii=False))
    return 0


def main(argv):
    if not argv or argv[0] not in {"ingest", "search", "ask"}:
        print(
            'Usage:\n'
            '  python main.py ingest <corpus_dir>\n'
            '  python main.py search --role <role> --k <n> "<question>"\n'
            '  python main.py ask --role <role> "<question>"',
            file=sys.stderr,
        )
        return 1

    command = argv[0]
    args = argv[1:]
    if command == "ingest":
        if len(args) != 1:
            print("Usage: python main.py ingest <corpus_dir>", file=sys.stderr)
            return 1
        return cmd_ingest(args[0])

    if "--role" not in args:
        print("Missing --role", file=sys.stderr)
        return 1
    role_at = args.index("--role")
    if role_at + 1 >= len(args):
        print("Missing role after --role", file=sys.stderr)
        return 1
    role = args[role_at + 1]
    rest = args[:role_at] + args[role_at + 2 :]

    k = None
    if command == "search":
        if "--k" not in rest:
            print("Usage: python main.py search --role <role> --k <n> \"<question>\"", file=sys.stderr)
            return 1
        k_at = rest.index("--k")
        if k_at + 1 >= len(rest):
            print("Missing value after --k", file=sys.stderr)
            return 1
        try:
            k = int(rest[k_at + 1])
        except ValueError:
            print("--k must be an integer", file=sys.stderr)
            return 1
        if k < 1:
            print("--k must be at least 1", file=sys.stderr)
            return 1
        rest = rest[:k_at] + rest[k_at + 2 :]

    question = " ".join(rest).strip()
    if not question:
        print("Missing question", file=sys.stderr)
        return 1
    if command == "search":
        return cmd_search(role, k, question)
    return cmd_ask(role, question)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
