import hashlib
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.config import CHUNK_OVERLAP, CHUNK_SIZE, EXTRACTED_DIR

META_FIELDS = ("doc_id", "company", "sector", "desk", "doc_type", "period")


def approx_tokens(text):
    return len(text.split())


def chunk_id(doc_id, page, content_type, index, text):
    raw = f"{doc_id}|{page}|{content_type}|{index}|{text}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{doc_id}:p{page}:{content_type}:{index}:{digest}"


def split_words(text, size, overlap):
    words = text.split()
    if not words:
        return []
    if len(words) <= size:
        return [text.strip()]
    step = max(1, size - overlap)
    out = []
    i = 0
    while i < len(words):
        out.append(" ".join(words[i : i + size]))
        if i + size >= len(words):
            break
        i += step
    return out


def split_table_rows(text, size, overlap):
    rows = [row for row in text.splitlines() if row.strip()]
    if not rows:
        return []
    packed = []
    current = []
    current_tokens = 0
    for row in rows:
        row_tokens = approx_tokens(row)
        if current and current_tokens + row_tokens > size:
            packed.append("\n".join(current))
            overlap_rows = []
            overlap_tokens = 0
            for prev in reversed(current):
                n = approx_tokens(prev)
                if overlap_tokens + n > overlap:
                    break
                overlap_rows.append(prev)
                overlap_tokens += n
            current = list(reversed(overlap_rows))
            current_tokens = overlap_tokens
        current.append(row)
        current_tokens += row_tokens
    if current:
        packed.append("\n".join(current))
    return packed


def _chunks_for_block(doc, page_num, content_type, text, size, overlap):
    text = (text or "").strip()
    if not text:
        return []
    pieces = (
        split_table_rows(text, size, overlap)
        if content_type == "table"
        else split_words(text, size, overlap)
    )
    meta = {key: doc.get(key, "") for key in META_FIELDS}
    out = []
    for index, piece in enumerate(pieces):
        out.append(
            {
                **meta,
                "page": page_num,
                "content_type": content_type,
                "chunk_index": index,
                "chunk_id": chunk_id(meta["doc_id"], page_num, content_type, index, piece),
                "text": piece,
                "n_tokens": approx_tokens(piece),
            }
        )
    return out


def chunk_document(doc, size=None, overlap=None):
    size = CHUNK_SIZE if size is None else size
    overlap = CHUNK_OVERLAP if overlap is None else overlap
    chunks = []
    for page in doc.get("pages") or []:
        page_num = page["page"]
        for block in page.get("content") or []:
            chunks.extend(
                _chunks_for_block(
                    doc,
                    page_num,
                    block.get("type") or "text",
                    block.get("text") or "",
                    size,
                    overlap,
                )
            )
    return chunks


def load_extracted(path):
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def extracted_json_files(extracted_dir=None):
    extracted_dir = Path(extracted_dir or EXTRACTED_DIR)
    if not extracted_dir.exists():
        return []
    files = []
    for path in sorted(extracted_dir.glob("*.json")):
        if path.name.startswith("_") or path.name.endswith(".tmp"):
            continue
        files.append(path)
    return files


def chunk_extracted_file(path, size=None, overlap=None):
    return chunk_document(load_extracted(path), size=size, overlap=overlap)


def chunk_all_extracted(extracted_dir=None, size=None, overlap=None):
    files = extracted_json_files(extracted_dir)
    all_chunks = []
    for path in files:
        chunks = chunk_extracted_file(path, size=size, overlap=overlap)
        all_chunks.extend(chunks)
        print(
            f"{path.stem}: {len(chunks)} chunks",
            flush=True,
        )
    return all_chunks


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        target = Path(sys.argv[1])
        chunks = chunk_extracted_file(target)
        print(f"file: {target.name}")
        print(f"chunks: {len(chunks)}")
    else:
        files = extracted_json_files()
        print(f"extracted json files: {len(files)}")
        chunks = chunk_all_extracted()
        print(f"total chunks: {len(chunks)}")
    if chunks:
        sample = chunks[0]
        print(f"sample chunk_id: {sample['chunk_id']}")
        print(f"sample page: {sample['page']}")
        print(f"sample type: {sample['content_type']}")
        print(sample["text"][:500])
