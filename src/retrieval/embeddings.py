import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.config import EMBEDDING_MODEL
from src.ingestion.chunker import chunk_extracted_file

_model = None


def get_model(name=None):
    global _model
    model_name = name or EMBEDDING_MODEL
    if _model is None or getattr(_model, "_gks_name", None) != model_name:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(model_name)
        _model._gks_name = model_name
    return _model


def embed_texts(texts, model_name=None):
    model = get_model(model_name)
    vectors = model.encode(
        list(texts),
        convert_to_numpy=True,
        show_progress_bar=len(texts) > 32,
    )
    return [vec.tolist() for vec in vectors]


def embed_chunks(chunks, model_name=None):
    if not chunks:
        return chunks
    vectors = embed_texts([chunk["text"] for chunk in chunks], model_name=model_name)
    for chunk, vector in zip(chunks, vectors):
        chunk["embedding"] = vector
        chunk["embedding_model"] = model_name or EMBEDDING_MODEL
    return chunks


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python src/retrieval/embeddings.py <extracted_json>")
        sys.exit(1)
    path = Path(sys.argv[1])
    chunks = chunk_extracted_file(path)
    print(f"model: {EMBEDDING_MODEL}")
    print(f"chunks: {len(chunks)}")
    embed_chunks(chunks)
    sample = chunks[0]
    print(f"dim: {len(sample['embedding'])}")
    print(f"chunk_id: {sample['chunk_id']}")
    print(f"page: {sample['page']}")
    print(sample["text"][:300])
