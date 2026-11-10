import csv
import hashlib
import json
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.config import EXTRACTED_DIR, MANIFEST_PATH, PDF_DIR
from src.ingestion.parser import extract_pages
from src.ingestion.table_extractor import extract_tables

META_FIELDS = ("doc_id", "company", "sector", "desk", "doc_type", "period")


def load_manifest(path=None):
    path = Path(path or MANIFEST_PATH)
    with open(path, encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    by_id = {}
    for row in rows:
        doc_id = (row.get("doc_id") or "").strip()
        if not doc_id:
            continue
        by_id[doc_id] = {key: (row.get(key) or "").strip() for key in META_FIELDS}
    return by_id


def manifest_for(doc_id, manifest=None):
    manifest = manifest if manifest is not None else load_manifest()
    if doc_id not in manifest:
        raise KeyError(f"doc_id not in manifest: {doc_id}")
    return manifest[doc_id]


def pdf_for(doc_id):
    pdf_path = PDF_DIR / f"{doc_id}.pdf"
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    return pdf_path


def _record(meta, page, text, content_type, extraction_status="success"):
    return {
        "doc_id": meta["doc_id"],
        "company": meta["company"],
        "sector": meta["sector"],
        "desk": meta["desk"],
        "doc_type": meta["doc_type"],
        "period": meta["period"],
        "page": page,
        "text": text or "",
        "content_type": content_type,
        "extraction_status": extraction_status,
    }


def combine_document(pdf_path=None, doc_id=None, manifest=None):
    """Text (parser) + tables (table_extractor) + manifest metadata. Page numbers 1-based."""
    pdf_path = Path(pdf_path) if pdf_path else None
    if doc_id is None:
        if pdf_path is None:
            raise ValueError("pdf_path or doc_id required")
        doc_id = pdf_path.stem
    if pdf_path is None:
        pdf_path = pdf_for(doc_id)

    meta = manifest_for(doc_id, manifest)
    pages = extract_pages(pdf_path, doc_id=doc_id)

    table_status = "success"
    tables = []
    try:
        tables = extract_tables(pdf_path, doc_id=doc_id)
    except Exception as exc:
        table_status = "failed"
        print(f"table extraction failed for {doc_id}: {exc}", file=sys.stderr)

    tables_by_page = {}
    for table in tables:
        tables_by_page.setdefault(table["page"], []).append(table)

    records = []
    for page in pages:
        records.append(
            _record(
                meta,
                page["page"],
                page.get("text") or "",
                "text",
                page.get("extraction_status") or "success",
            )
        )
        for table in tables_by_page.get(page["page"], []):
            records.append(
                _record(meta, page["page"], table.get("text") or "", "table")
            )

    if table_status == "failed":
        for rec in records:
            if rec["content_type"] == "text":
                rec["table_extraction_status"] = "failed"

    return records


def records_to_document(records):
    if not records:
        raise ValueError("no records to save")
    meta = {key: records[0][key] for key in META_FIELDS}
    pages = []
    by_page = {}
    for rec in records:
        page_num = rec["page"]
        if page_num not in by_page:
            block = {"page": page_num, "content": []}
            by_page[page_num] = block
            pages.append(block)
        item = {"type": rec["content_type"], "text": rec["text"]}
        if rec.get("extraction_status") and rec["extraction_status"] != "success":
            item["extraction_status"] = rec["extraction_status"]
        by_page[page_num]["content"].append(item)

    failed_text = [
        rec["page"]
        for rec in records
        if rec["content_type"] == "text" and rec.get("extraction_status") == "failed"
    ]
    table_failed = any(rec.get("table_extraction_status") == "failed" for rec in records)
    table_n = sum(1 for rec in records if rec["content_type"] == "table")
    if failed_text and table_failed:
        status = "failed"
    elif table_failed:
        status = "tables_failed"
    elif failed_text:
        status = "partial"
    else:
        status = "success"

    doc = {
        **meta,
        "extraction_status": status,
        "page_count": len(pages),
        "table_count": table_n,
        "pages": pages,
    }
    if table_failed:
        doc["table_extraction_status"] = "failed"
    if failed_text:
        doc["failed_text_pages"] = failed_text
    return doc


def log_extraction_issue(doc_id, message, out_dir=None):
    out_dir = Path(out_dir or EXTRACTED_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "_extraction_errors.jsonl"
    with open(log_path, "a", encoding="utf-8") as file:
        file.write(json.dumps({"doc_id": doc_id, "error": message}) + "\n")
    print(f"logged: {doc_id}: {message}", file=sys.stderr)


def save_document(records, out_dir=None):
    out_dir = Path(out_dir or EXTRACTED_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = records_to_document(records)
    path = out_dir / f"{doc['doc_id']}.json"
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(doc, file, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    if doc["extraction_status"] != "success":
        log_extraction_issue(doc["doc_id"], doc["extraction_status"])
    return path, doc


PDF_HASH_NAME = "_pdf_hashes.json"


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _pdf_hash_path(out_dir):
    return Path(out_dir) / PDF_HASH_NAME


def load_pdf_hashes(out_dir):
    path = _pdf_hash_path(out_dir)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as file:
        data = json.load(file)
    return data if isinstance(data, dict) else {}


def save_pdf_hashes(out_dir, hashes):
    path = _pdf_hash_path(out_dir)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(hashes, file, indent=2, sort_keys=True)
    os.replace(tmp, path)


def existing_json_names(out_dir):
    try:
        return {
            name.lower()
            for name in os.listdir(out_dir)
            if name.lower().endswith(".json") and not name.startswith("_")
        }
    except FileNotFoundError:
        return set()


def ingest_one(pdf_path=None, doc_id=None, manifest=None, out_dir=None):
    records = combine_document(pdf_path=pdf_path, doc_id=doc_id, manifest=manifest)
    return save_document(records, out_dir=out_dir)


def ingest_all(manifest=None, out_dir=None, force=False, pdf_dir=None):
    """Extract every manifest filing under ``pdf_dir``.

    An unchanged PDF is skipped. A changed PDF is extracted again. Chunk
    replacement, so the new file does not duplicate the old one, happens when
    the extracted JSON is indexed.
    """
    manifest = manifest if manifest is not None else load_manifest()
    pdf_dir = Path(pdf_dir or PDF_DIR)
    out_dir = Path(out_dir or EXTRACTED_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    hashes = {} if force else load_pdf_hashes(out_dir)
    dirty = False
    total = len(manifest)
    saved = 0
    skipped = 0
    failed = 0
    print(f"pdfs: {pdf_dir.resolve()}", flush=True)
    print(f"output: {out_dir.resolve()}", flush=True)
    for i, doc_id in enumerate(manifest, start=1):
        pdf_path = pdf_dir / f"{doc_id}.pdf"
        json_path = out_dir / f"{doc_id}.json"
        if not pdf_path.exists():
            failed += 1
            message = f"PDF not found: {pdf_path}"
            log_extraction_issue(doc_id, message, out_dir=out_dir)
            print(f"[{i}/{total}] FAIL {doc_id}: {message}", flush=True)
            continue
        digest = file_sha256(pdf_path)
        if not force and json_path.exists() and hashes.get(doc_id) == digest:
            skipped += 1
            print(f"[{i}/{total}] skip {doc_id}", flush=True)
            continue
        if not force and json_path.exists() and doc_id not in hashes:
            hashes[doc_id] = digest
            dirty = True
            skipped += 1
            print(f"[{i}/{total}] skip {doc_id} (recorded hash)", flush=True)
            continue
        print(f"[{i}/{total}] start {doc_id} ...", flush=True)
        try:
            _, doc = ingest_one(
                pdf_path=pdf_path,
                doc_id=doc_id,
                manifest=manifest,
                out_dir=out_dir,
            )
            hashes[doc_id] = digest
            dirty = True
            saved += 1
            print(
                f"[{i}/{total}] saved {doc_id}  "
                f"pages={doc['page_count']} tables={doc['table_count']}  "
                f"status={doc['extraction_status']}  "
                f"(saved={saved} skip={skipped} fail={failed})",
                flush=True,
            )
        except Exception as exc:
            failed += 1
            log_extraction_issue(doc_id, str(exc), out_dir=out_dir)
            print(f"[{i}/{total}] FAIL {doc_id}: {exc}", flush=True)
    if dirty:
        save_pdf_hashes(out_dir, hashes)
    return saved, skipped, failed


def _print_summary(path, doc):
    print(f"saved: {path}")
    print(f"doc_id: {doc['doc_id']}")
    print(f"company: {doc['company']}")
    print(f"doc_type: {doc['doc_type']}")
    print(f"period: {doc['period']}")
    print(f"pages: {doc['page_count']}")
    print(f"table records: {doc['table_count']}")
    print(f"extraction_status: {doc['extraction_status']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python src/ingestion/ingest.py <pdf_path_or_doc_id|--all> [--force]")
        sys.exit(1)

    args = sys.argv[1:]
    force = "--force" in args
    args = [a for a in args if a != "--force"]
    target = args[0] if args else "--all"

    if target == "--all":
        saved, skipped, failed = ingest_all(force=force)
        print(f"done. saved={saved} skipped={skipped} failed={failed}")
        sys.exit(1 if failed else 0)

    path = Path(target)
    if path.suffix.lower() == ".pdf":
        out_path, doc = ingest_one(pdf_path=path)
    else:
        out_path, doc = ingest_one(doc_id=target)
    _print_summary(out_path, doc)
