"""One row per manifest PDF: pages, table counts, and a guessed statement type.

Types are not stored by the extractor. They are inferred from the table text.
"""

import csv
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.config import EXTRACTED_DIR, MANIFEST_PATH, PDF_DIR

OUT_PATH = PROJECT_DIR / "pdf_inventory.csv"
TYPES = (
    "balance_sheet",
    "income_statement",
    "cash_flow",
    "equity",
    "selected_financials",
    "other",
)


def table_type(text):
    folded = " ".join((text or "").lower().split())
    if any(
        phrase in folded
        for phrase in (
            "cash flows from operating",
            "net cash provided by operating",
            "net cash used in operating",
            "cash provided by operating activities",
            "cash used in operating activities",
        )
    ):
        return "cash_flow"
    if "total assets" in folded and (
        "total liabilities" in folded or "cash and cash equivalents" in folded
    ):
        return "balance_sheet"
    if any(
        phrase in folded
        for phrase in ("net sales", "total revenue", "net revenue", "total net revenue")
    ) and any(phrase in folded for phrase in ("net income", "operating income")):
        return "income_statement"
    if "balance at" in folded and any(
        phrase in folded
        for phrase in (
            "reacquired stock",
            "treasury stock",
            "retained earnings",
            "stockholders' equity",
            "shareholders' equity",
        )
    ):
        return "equity"
    if "fiscal years" in folded and any(
        phrase in folded for phrase in ("revenue", "net sales")
    ) and any(phrase in folded for phrase in ("working capital", "employees")):
        return "selected_financials"
    return "other"


def pages_cell(pages):
    return ";".join(str(page) for page in pages)


def load_manifest():
    with open(MANIFEST_PATH, encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def inventory_row(meta):
    doc_id = meta["doc_id"]
    pdf_path = PDF_DIR / f"{doc_id}.pdf"
    row = {
        "pdf_file": pdf_path.name if pdf_path.exists() else "",
        "doc_id": doc_id,
        "company": meta.get("company", ""),
        "sector": meta.get("sector", ""),
        "desk": meta.get("desk", ""),
        "category": meta.get("doc_type", ""),
        "period": meta.get("period", ""),
        "page_count": "",
        "table_count": "",
        "extraction_status": "pending",
        "table_pages": "",
    }
    for name in TYPES:
        row[f"{name}_count"] = ""
        row[f"{name}_pages"] = ""

    path = EXTRACTED_DIR / f"{doc_id}.json"
    if not path.exists():
        return row

    with open(path, encoding="utf-8") as file:
        doc = json.load(file)

    grouped = {name: [] for name in TYPES}
    table_pages = []
    for page in doc.get("pages") or []:
        page_num = page.get("page")
        for block in page.get("content") or []:
            if block.get("type") != "table":
                continue
            table_pages.append(page_num)
            grouped[table_type(block.get("text") or "")].append(page_num)

    row["page_count"] = doc.get("page_count", "")
    row["table_count"] = doc.get("table_count", len(table_pages))
    row["extraction_status"] = doc.get("extraction_status", "")
    row["table_pages"] = pages_cell(table_pages)
    for name in TYPES:
        row[f"{name}_count"] = len(grouped[name])
        row[f"{name}_pages"] = pages_cell(grouped[name])
    return row


def write_inventory(path=OUT_PATH):
    rows = [inventory_row(meta) for meta in load_manifest()]
    fieldnames = list(rows[0].keys()) if rows else []
    with open(path, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    ready = sum(1 for row in rows if row["extraction_status"] != "pending")
    return path, len(rows), ready


if __name__ == "__main__":
    path, total, ready = write_inventory()
    print(f"wrote: {path}")
    print(f"pdfs: {total}")
    print(f"with json: {ready}")
    print(f"pending: {total - ready}")
