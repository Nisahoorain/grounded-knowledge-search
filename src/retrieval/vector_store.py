import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.config import CHROMA_DIR, PROJECT_DIR as CONFIG_ROOT
from src.ingestion.chunker import chunk_extracted_file
from src.retrieval.embeddings import embed_chunks

COLLECTION_NAME = "gks_chunks"
BATCH = 200
META_KEYS = ("doc_id", "company", "sector", "desk", "doc_type", "period", "page", "content_type")

_client = None


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
    return get_client().get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


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


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python src/retrieval/vector_store.py <extracted_json>")
        sys.exit(1)
    path = Path(sys.argv[1])
    chunks = chunk_extracted_file(path)
    n = upsert_chunks(chunks)
    print(f"upserted: {n}")
    print(f"collection: {COLLECTION_NAME}")
    print(f"total in chroma: {collection_count()}")
    print(f"path: {chroma_path()}")
    print(f"sample id: {chunks[0]['chunk_id']}")
