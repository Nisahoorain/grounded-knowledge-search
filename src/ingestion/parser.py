import sys
from pathlib import Path

import fitz


def clean_page_text(text):
    lines = []
    for line in text.splitlines():
        if line.strip().lower() == "table of contents":
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def extract_pages(pdf_path, doc_id=None):
    """Extract text page by page. Page numbers are 1-based."""
    pdf_path = Path(pdf_path)

    if doc_id is None:
        doc_id = pdf_path.stem

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    try:
        pdf = fitz.open(pdf_path)
    except Exception as exc:
        raise RuntimeError(f"Could not open PDF {pdf_path}: {exc}") from exc

    pages = []

    try:
        for index in range(len(pdf)):
            page_number = index + 1
            extraction_status = "success"
            try:
                text = pdf[index].get_text("text") or ""
                text = clean_page_text(text)
            except Exception as exc:
                extraction_status = "failed"
                print(
                    f"page {page_number} failed in {doc_id}: {exc}",
                    file=sys.stderr,
                )
                text = ""

            pages.append({
                "doc_id": doc_id,
                "page": page_number,
                "text": text,
                "content_type": "text",
                "extraction_status": extraction_status,
            })
    finally:
        pdf.close()

    return pages


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python src/ingestion/parser.py <pdf_path>")
        sys.exit(1)

    extracted = extract_pages(sys.argv[1])
    failed = [p["page"] for p in extracted if p["extraction_status"] == "failed"]
    print(f"pages: {len(extracted)}")
    print(f"failed pages: {failed if failed else 0}")

    sample = extracted[min(2, len(extracted) - 1)]
    print(f"doc_id: {sample['doc_id']}")
    print(f"page: {sample['page']}")
    print(f"content_type: {sample['content_type']}")
    print(f"extraction_status: {sample['extraction_status']}")
    print(f"chars: {len(sample['text'])}")
    print(sample["text"][:500])
