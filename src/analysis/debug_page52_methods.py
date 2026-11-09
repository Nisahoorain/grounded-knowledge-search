"""Inspect 3M 2015 10-K page 52 extraction methods separately. Read-only."""
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import pdfplumber
from src.config import PDF_DIR
from src.ingestion.table_extractor import (
    extract_method,
    layout_rows,
    merge_unique,
    normalize,
    row_key,
    row_quality,
    serialize_rows,
)

PDF = PDF_DIR / "3M_2015_10K.pdf"
PAGE = 52

LINES = {"vertical_strategy": "lines", "horizontal_strategy": "lines"}
TEXT = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "text",
    "snap_x_tolerance": 8,
    "snap_y_tolerance": 6,
}


def label_seq(rows):
    out = []
    for i, row in enumerate(rows, 1):
        body = row.rsplit(" [Page:", 1)[0]
        first = body.split("|")[0].strip()[:70]
        out.append(f"{i:02d}. {first}")
    return out


def dump_raw_tables(page, settings, name):
    print(f"\n===== RAW pdfplumber tables: {name} =====")
    try:
        tables = page.extract_tables(table_settings=settings) or []
    except Exception as exc:
        print(f"extract_tables failed: {exc}")
        return []
    print(f"table count: {len(tables)}")
    all_first = []
    for ti, table in enumerate(tables, 1):
        print(f"\n--- table {ti} ({len(table)} rows) ---")
        for ri, row in enumerate(table, 1):
            cells = normalize(row)
            first = cells[0][:70] if cells else "<empty>"
            n_amt = sum(1 for c in cells if c)
            preview = " | ".join(cells[:6])[:120]
            print(f"  raw {ri:02d} [{n_amt} cells] {preview}")
            all_first.append(first)
    return tables


def main():
    print(f"PDF: {PDF}")
    print(f"exists: {PDF.exists()}")
    with pdfplumber.open(PDF) as pdf:
        page = pdf.pages[PAGE - 1]
        print(f"page.page_number={page.page_number} size={page.width:.0f}x{page.height:.0f}")

        raw_lines = dump_raw_tables(page, LINES, "lines/lines")
        raw_text = dump_raw_tables(page, TEXT, "lines/text")

        print("\n===== serialize_rows on each raw table (lines/lines) =====")
        for ti, table in enumerate(raw_lines, 1):
            ser = serialize_rows(table, page.page_number)
            print(f"\n-- table {ti} serialized ({len(ser)} rows) --")
            for line in label_seq(ser):
                print(" ", line)

        print("\n===== serialize_rows on each raw table (lines/text) =====")
        for ti, table in enumerate(raw_text, 1):
            ser = serialize_rows(table, page.page_number)
            print(f"\n-- table {ti} serialized ({len(ser)} rows) --")
            for line in label_seq(ser):
                print(" ", line)

        lines_out = extract_method(page, LINES)
        text_out = extract_method(page, TEXT)
        layout_out = layout_rows(page)
        merged = merge_unique(lines_out + text_out + layout_out)

        print("\n===== METHOD OUTPUTS (first-cell labels) =====")
        print(f"\n--- extract_method LINES ({len(lines_out)} rows) ---")
        for line in label_seq(lines_out):
            print(" ", line)
        print(f"\n--- extract_method TEXT ({len(text_out)} rows) ---")
        for line in label_seq(text_out):
            print(" ", line)
        print(f"\n--- layout_rows ({len(layout_out)} rows) ---")
        for line in label_seq(layout_out):
            print(" ", line)
        print(f"\n--- merge_unique ({len(merged)} rows) ---")
        for line in label_seq(merged):
            print(" ", line)

        print("\n===== FULL ROWS: LINES =====")
        for r in lines_out:
            print(r)
        print("\n===== FULL ROWS: TEXT =====")
        for r in text_out:
            print(r)
        print("\n===== FULL ROWS: LAYOUT =====")
        for r in layout_out:
            print(r)
        print("\n===== FULL ROWS: MERGED =====")
        for r in merged:
            print(r)

        print("\n===== merge_unique attribution =====")
        sources = [
            ("lines", lines_out),
            ("text", text_out),
            ("layout", layout_out),
        ]
        seen = {}
        order = []
        for src_name, rows in sources:
            for row in rows:
                key = row_key(row)
                if key not in seen:
                    seen[key] = {"winner": row, "src": src_name, "replaced": False}
                    order.append(key)
                    continue
                if row_quality(row) > row_quality(seen[key]["winner"]):
                    seen[key]["winner"] = row
                    seen[key]["src"] = src_name + " (quality replace)"
                    seen[key]["replaced"] = True
        for i, key in enumerate(order, 1):
            info = seen[key]
            body = info["winner"].rsplit(" [Page:", 1)[0]
            first = body.split("|")[0].strip()[:60]
            print(f"{i:02d}. src={info['src']:<22} {first}")

        print("\n===== row_key collisions across methods =====")
        from collections import defaultdict

        by_key = defaultdict(list)
        for src_name, rows in sources:
            for row in rows:
                by_key[row_key(row)].append((src_name, row))
        collisions = {k: v for k, v in by_key.items() if len(v) > 1}
        print(f"keys with >1 method hit: {len(collisions)}")
        for key, hits in collisions.items():
            print(f"\nkey label={key[0]!r} nums={key[1]}")
            for src, row in hits:
                print(f"  {src}: {row.rsplit(' [Page:', 1)[0][:140]}")


if __name__ == "__main__":
    main()
