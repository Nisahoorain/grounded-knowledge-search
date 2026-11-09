import hashlib
import json
import re
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.config import CHUNK_OVERLAP, CHUNK_SIZE, EXTRACTED_DIR

META_FIELDS = ("doc_id", "company", "sector", "desk", "doc_type", "period")
CHUNKING_VERSION = "table-row-v3"
_YEAR_HEADER = re.compile(r"\b(?:19|20)\d{2}\b")
_STATEMENT_HEADING = re.compile(
    r"(?i)^\s*"
    r"(?:(?:consolidated|condensed|combined)\s+)*"
    r"(?:"
    r"statements?\s+of\s+"
    r"(?:"
    r"cash\s+flows?"
    r"|(?:income(?:\s*\(loss\))?|\(loss\)\s+income)"
    r"|earnings"
    r"|operations"
    r"|comprehensive\s+(?:\(?loss\)?\s+)?income"
    r"|comprehensive\s+loss"
    r"|(?:changes\s+in\s+)?"
    r"(?:(?:stockholders|shareholders|members)[’']?\s+)?"
    r"equity(?:\s+\(deficit\))?"
    r"|financial\s+position"
    r")"
    r"|balance\s+sheets?"
    r"|income\s+statements?"
    r")"
    r"(?:\s+\((?:continued|unaudited)\))?"
    r"(?:\s+\(\d+\))*"
    r"\s*[.:;]?\s*$"
)

def extract_statement_heading(page):
    for block in page.get("content") or []:
        if block.get("type") != "text":
            continue

        for line in (block.get("text") or "").splitlines():
            original = line.strip()
            if not original:
                continue

            readable = re.sub(r"\s+", " ", original).strip()

            # Repair common OCR splits before matching.
            readable = re.sub(
                r"(?i)\b(incom)\s+e\b", r"\1e", readable
            )
            readable = re.sub(
                r"(?i)\b(equit)\s+y\b", r"\1y", readable
            )
            readable = re.sub(
                r"(?i)\b(flow)\s+s\b", r"\1s", readable
            )
            readable = re.sub(
                r"(?i)\b(shee)\s+t\b", r"\1t", readable
            )
            readable = re.sub(
                r"(?i)\bcomp\s+rehensive\b", "comprehensive", readable
            )
            readable = re.sub(
                r"(?i)\bstatem\s+ents\b", "statements", readable
            )

            # Reject entirely lowercase fragments that are not statement titles.
            if readable == readable.lower():
                continue

            if _STATEMENT_HEADING.match(readable):
                return original

    return ""

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
    """One data row per chunk. A year header is repeated on each row.

    Packing a whole statement into one chunk still fits ``size``, but the
    embedding of that chunk is pulled toward the other rows. ``size`` and
    ``overlap`` stay in the signature for the text chunker.
    """
    del size, overlap
    rows = [row.strip() for row in text.splitlines() if row.strip()]
    if not rows:
        return []
    header = rows[0] if len(_YEAR_HEADER.findall(rows[0])) >= 2 else ""
    body = rows[1:] if header else rows
    if not body:
        return [header]
    if header:
        return [f"{header}\n{row}" for row in body]
    return body


def _chunks_for_block(doc, page_num, content_type, text, size, overlap, page_context=""):
    text = (text or "").strip()
    if not text:
        return []
    pieces = (
        split_table_rows(text, size, overlap)
        if content_type == "table"
        else split_words(text, size, overlap)
    )
    if content_type == "table":
        company = str(doc.get("company") or "").strip()
        period = str(doc.get("period") or "").strip()
        context = " ".join(part for part in (company, period, page_context) if part)
        if context:
            pieces = [f"{context}\n{piece}" for piece in pieces]
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
        statement_heading = extract_statement_heading(page)
        for block in page.get("content") or []:
            chunks.extend(
                _chunks_for_block(
                    doc,
                    page_num,
                    block.get("type") or "text",
                    block.get("text") or "",
                    size,
                    overlap,
                    page_context=statement_heading,
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
