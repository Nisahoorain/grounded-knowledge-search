"""Trace page 50 rows that the income statement drops or relabels. Read-only."""
import re
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import pdfplumber
from src.config import PDF_DIR
from src.ingestion.table_extractor import (
    _cells,
    explode_dashes,
    is_amount,
    is_prose,
    is_shattered,
    keep_row,
    merge_units,
    normalize,
    peel_amounts,
    real_amounts,
)

PDF = PDF_DIR / "3M_2015_10K.pdf"
PAGE = 50

TARGETS = (
    "Interest income",
    "attributable to noncontrolling interest",
    "Weighted average 3M common shares outstanding — basic",
    "Weighted average 3M common shares outstanding — diluted",
    "shareholders — basic",
    "shareholders — diluted",
    "Total interest expense",
)


def parts_of(line):
    cleaned = re.sub(r"\.{2,}", "  ", line)
    return [p for p in re.split(r" {2,}", cleaned.strip()) if p.strip()]


def trace(line):
    parts = parts_of(line)
    exploded = explode_dashes(_cells(parts))
    peeled = peel_amounts(exploded)
    merged = merge_units(peeled)
    cells = normalize(parts)
    print("original:", repr(line.strip()))
    print("parts:   ", parts)
    if peeled != parts:
        print("peeled:  ", peeled)
    if merged != peeled:
        print("merged:  ", merged)
    print("normalize:", cells)
    print(
        "real_amounts:",
        real_amounts(cells) if cells else 0,
        " is_prose:",
        is_prose(cells) if cells else None,
        " is_shattered:",
        is_shattered(cells) if cells else None,
    )
    print("keep_row:", keep_row(parts))
    if cells:
        flags = ", ".join(f"{c!r} amount={is_amount(c)}" for c in cells)
        print("is_amount:", flags)
    print()


def main():
    with pdfplumber.open(PDF) as pdf:
        page = pdf.pages[PAGE - 1]
        text = page.extract_text(layout=True) or ""
    seen = 0
    for line in text.splitlines():
        if not any(needle in line for needle in TARGETS):
            continue
        if "including noncontrolling" in line:
            continue
        trace(line)
        seen += 1
    print(f"traced {seen} lines")
    return 0 if seen == 7 else 1


if __name__ == "__main__":
    sys.exit(main())
