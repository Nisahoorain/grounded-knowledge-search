import hashlib
import json
import os
import sys
import time
from pathlib import Path
from src.retrieval.embeddings import embed_texts

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.config import CHROMA_DIR, PROJECT_DIR as CONFIG_ROOT
from src.ingestion.chunker import CHUNKING_VERSION, chunk_extracted_file, extracted_json_files
from src.retrieval.embeddings import embed_chunks

COLLECTION_NAME = "gks_chunks"
HASH_NAME = "index_hashes.json"
BATCH = 200
META_KEYS = (
    "doc_id",
    "company",
    "sector",
    "desk",
    "doc_type",
    "period",
    "page",
    "content_type",
    "statement",
    "section",
    "columns",
)

_client = None
_collection = None


def chroma_path():
    path = Path(CHROMA_DIR)
    if not path.is_absolute():
        path = CONFIG_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_client():
    global _client
    if _client is None:
        import chromadb

        _client = chromadb.PersistentClient(path=str(chroma_path()))
    return _client


def get_collection():
    global _collection

    if _collection is None:
        _collection = get_client().get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={
                "hnsw:space": "cosine",
                "hnsw:construction_ef": 100,
                "hnsw:M": 16,
                "hnsw:search_ef": 50,
            },
        )

    return _collection

def _meta(chunk):
    out = {}
    for key in META_KEYS:
        value = chunk.get(key, "")
        if value is None:
            value = ""
        if key == "page":
            out[key] = int(value)
        else:
            out[key] = str(value)
    out["chunk_id"] = str(chunk["chunk_id"])
    return out


def upsert_chunks(chunks):
    if not chunks:
        return 0
    if "embedding" not in chunks[0]:
        embed_chunks(chunks)
    collection = get_collection()
    for start in range(0, len(chunks), BATCH):
        batch = chunks[start : start + BATCH]
        collection.upsert(
            ids=[c["chunk_id"] for c in batch],
            embeddings=[c["embedding"] for c in batch],
            documents=[c["text"] for c in batch],
            metadatas=[_meta(c) for c in batch],
        )
    return len(chunks)


def collection_count():
    return get_collection().count()


def hash_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def hash_store_path():
    return chroma_path() / HASH_NAME


def load_hashes():
    path = hash_store_path()
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as file:
        data = json.load(file)
    return data if isinstance(data, dict) else {}


def save_hashes(hashes):
    path = hash_store_path()
    payload = json.dumps(hashes, indent=2, sort_keys=True) + "\n"
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as file:
        file.write(payload)
    for _ in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.2)
    with open(path, "w", encoding="utf-8") as file:
        file.write(payload)
    try:
        tmp.unlink()
    except OSError:
        pass


def doc_has_chunks(doc_id):
    found = get_collection().get(where={"doc_id": doc_id}, limit=1, include=["metadatas"])
    return bool(found.get("ids"))


def delete_doc(doc_id):
    get_collection().delete(where={"doc_id": doc_id})


def index_file(path, hashes=None):
    path = Path(path)
    doc_id = path.stem
    digest = f"{CHUNKING_VERSION}:{hash_file(path)}"
    own_store = hashes is None
    if hashes is None:
        hashes = load_hashes()
    if hashes.get(doc_id) == digest:
        return "skip", 0
    already = doc_has_chunks(doc_id)
    if hashes.get(doc_id) is None and already:
        hashes[doc_id] = digest
        if own_store:
            save_hashes(hashes)
        return "recorded", 0
    if already:
        delete_doc(doc_id)
        action = "replaced"
    else:
        action = "indexed"
    n = upsert_chunks(chunk_extracted_file(path))
    hashes[doc_id] = digest
    if own_store:
        save_hashes(hashes)
    return action, n


def index_all_extracted():
    files = extracted_json_files()
    total_files = len(files)
    hashes = load_hashes()
    counts = {"skip": 0, "recorded": 0, "indexed": 0, "replaced": 0}
    upserted = 0
    for index, path in enumerate(files, start=1):
        action, n = index_file(path, hashes)
        if action != "skip":
            save_hashes(hashes)
        counts[action] += 1
        upserted += n
        if action == "skip":
            detail = "skip"
        elif action == "recorded":
            detail = "recorded hash"
        else:
            detail = f"{action} {n} chunks"
        print(
            f"[{index}/{total_files}] {path.stem}: {detail}  chroma={collection_count()}",
            flush=True,
        )
    return upserted, total_files, counts


def indexed_doc_ids(page_size=1000):
    collection = get_collection()
    found = set()
    offset = 0
    while True:
        batch = collection.get(include=["metadatas"], limit=page_size, offset=offset)
        metas = batch.get("metadatas") or []
        if not metas:
            break
        for meta in metas:
            if meta and meta.get("doc_id"):
                found.add(meta["doc_id"])
        offset += len(metas)
        if len(metas) < page_size:
            break
    return found


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python src/retrieval/vector_store.py <extracted_json|--all>")
        sys.exit(1)
    if sys.argv[1] == "--all":
        upserted, file_count, counts = index_all_extracted()
        doc_ids = indexed_doc_ids()
        expected = {path.stem for path in extracted_json_files()}
        missing = sorted(expected - doc_ids)
        print(f"collection: {COLLECTION_NAME}")
        print(f"path: {chroma_path()}")
        print(f"files indexed: {len(doc_ids)}/{file_count}")
        print(
            "skip={skip} recorded={recorded} indexed={indexed} replaced={replaced}".format(
                **counts
            )
        )
        print(f"chunks embedded this run: {upserted}")
        print(f"total in chroma: {collection_count()}")
        if missing:
            print(f"missing: {', '.join(missing)}")
            sys.exit(1)
        sys.exit(0)
    path = Path(sys.argv[1])
    action, n = index_file(path)
    print(f"{action}: {path.stem}")
    print(f"chunks embedded this run: {n}")
    print(f"collection: {COLLECTION_NAME}")
    print(f"total in chroma: {collection_count()}")
    print(f"path: {chroma_path()}")


ROLE_DESKS = {
    "consumer": ["consumer"],
    "health_industrials": ["health_industrials"],
    "tech_telecom": ["tech_telecom"],
    "lead": ["consumer", "health_industrials", "tech_telecom"],
}


def search_chunks(question, role, k=5, doc_ids=None):
    if role not in ROLE_DESKS:
        raise ValueError(f"Invalid role: {role}")

    if k < 1:
        raise ValueError("k must be at least 1")

    collection = get_collection()

    query_args = {
        "n_results": k,
        "include": ["documents", "metadatas", "distances"],
    }

    conds = []
    if role != "lead":
        conds.append({"desk": role})
    if doc_ids:
        conds.append({"doc_id": {"$in": list(doc_ids)}})

    if len(conds) == 1:
        query_args["where"] = conds[0]
    elif conds:
        query_args["where"] = {"$and": conds}

    t0 = time.perf_counter()

    query_vector = embed_texts([question])[0]

    t1 = time.perf_counter()

    query_args["query_embeddings"] = [query_vector]

    results = collection.query(**query_args)

    t2 = time.perf_counter()

    print(
        f"Embedding: {t1-t0:.2f}s | "
        f"Chroma search: {t2-t1:.2f}s",
        file=sys.stderr,
    )

    output = []

    for document, metadata, distance in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        output.append({
            "doc_id": metadata.get("doc_id"),
            "page": metadata.get("page"),
            "chunk_id": metadata.get("chunk_id"),
            "score": round(1.0 - float(distance), 4),
            "text": document,
        })

    return output