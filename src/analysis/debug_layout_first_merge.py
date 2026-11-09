"""Layout-first merge experiment. Does not modify table_extractor.py."""
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
    row_key,
    row_quality,
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


def body(row):
    return row.rsplit(" [Page:", 1)[0]


def first_cell(row):
    return body(row).split("|")[0].strip()


def layout_first_merge(layout, candidates):
    """Keep layout order. Replace a row only when a candidate shares row_key and scores higher.

    Candidate-only keys are not appended. Equal quality keeps the layout row.
    """
    best = {}
    for row in layout:
        key = row_key(row)
        if key not in best or row_quality(row) > row_quality(best[key]):
            best[key] = row

    substitutions = []
    dropped = []
    for row in candidates:
        key = row_key(row)
        if key not in best:
            dropped.append(row)
            continue
        if row_quality(row) > row_quality(best[key]):
            substitutions.append((best[key], row))
            best[key] = row

    out = []
    seen = set()
    for row in layout:
        key = row_key(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(best[key])
    return out, substitutions, dropped


def index_where(rows, pred):
    hits = [i for i, row in enumerate(rows) if pred(first_cell(row).lower())]
    return hits


def check_acceptance(rows):
    labels = [first_cell(r).lower() for r in rows]
    checks = []

    def add(name, ok, detail):
        checks.append((name, ok, detail))

    total_assets = index_where(rows, lambda s: s == "total assets")
    total_current = index_where(rows, lambda s: s.startswith("total current assets"))
    wip = index_where(rows, lambda s: s.startswith("work in process"))
    finished = index_where(rows, lambda s: s.startswith("finished goods"))
    raw = index_where(rows, lambda s: s.startswith("raw materials"))
    goodwill = index_where(rows, lambda s: s.startswith("goodwill"))
    ppe_net = index_where(
        rows, lambda s: "property, plant and equipment" in s
    )
    intangible = index_where(rows, lambda s: s.startswith("intangible assets"))
    total_le = index_where(rows, lambda s: s.startswith("total liabilities and equity"))
    keys = [row_key(r) for r in rows]

    add(
        "total assets present once",
        len(total_assets) == 1,
        f"indexes={total_assets}",
    )
    add(
        "total assets after total current assets",
        len(total_assets) == 1
        and len(total_current) == 1
        and total_assets[0] > total_current[0],
        f"current={total_current} assets={total_assets}",
    )
    add(
        "work in process between finished goods and raw materials",
        len(wip) == 1
        and len(finished) == 1
        and len(raw) == 1
        and finished[0] < wip[0] < raw[0],
        f"finished={finished} wip={wip} raw={raw}",
    )
    add(
        "goodwill after net PP&E and before intangible assets",
        len(goodwill) == 1
        and len(ppe_net) >= 1
        and len(intangible) == 1
        and max(ppe_net) < goodwill[0] < intangible[0],
        f"ppe={ppe_net} goodwill={goodwill} intangible={intangible}",
    )
    add(
        "total liabilities and equity is the last row",
        len(total_le) == 1 and total_le[0] == len(rows) - 1,
        f"index={total_le} n={len(rows)} last={labels[-1] if labels else None!r}",
    )
    add(
        "no duplicate row keys",
        len(keys) == len(set(keys)),
        f"rows={len(keys)} unique={len(set(keys))}",
    )

    number_changes = []
    for row in rows:
        key = row_key(row)
        # key's numeric tuple is the row's numbers; compare to itself is tautological.
        # Flag only if the kept row's numbers differ from the layout row with the same key.
        # Caller passes layout map separately; filled below if needed.
        _ = key
    return checks, number_changes


def number_stability(layout, merged):
    layout_by_key = {}
    for row in layout:
        layout_by_key.setdefault(row_key(row), row)
    changes = []
    for row in merged:
        key = row_key(row)
        original = layout_by_key.get(key)
        if original is None:
            changes.append(("added", body(row)))
            continue
        if body(original) != body(row):
            changes.append((body(original), body(row)))
    return changes


def main():
    with pdfplumber.open(PDF) as pdf:
        page = pdf.pages[PAGE - 1]
        lines = extract_method(page, LINES)
        text = extract_method(page, TEXT)
        layout = layout_rows(page)

    old = merge_unique(lines + text + layout)
    new, substitutions, dropped = layout_first_merge(layout, lines + text)

    print(f"lines={len(lines)} text={len(text)} layout={len(layout)}")
    print(f"old merge_unique={len(old)}  layout-first={len(new)}")
    print(f"substitutions={len(substitutions)}  candidate-only dropped={len(dropped)}")

    print("\n===== LAYOUT-FIRST ORDER =====")
    for i, row in enumerate(new, 1):
        print(f"{i:02d}. {body(row)}")

    print("\n===== SUBSTITUTIONS (layout -> better candidate) =====")
    if not substitutions:
        print("(none)")
    for before, after in substitutions:
        print(f"  quality {row_quality(before)} -> {row_quality(after)}")
        print(f"    layout:    {body(before)}")
        print(f"    candidate: {body(after)}")

    print("\n===== CANDIDATE ROWS NOT IN LAYOUT (not appended) =====")
    seen_drop = set()
    for row in dropped:
        key = row_key(row)
        if key in seen_drop:
            continue
        seen_drop.add(key)
        print(f"  {body(row)}")

    print("\n===== CELL TEXT THAT CHANGED VS LAYOUT =====")
    changes = number_stability(layout, new)
    if not changes:
        print("(none — every kept row is byte-identical to layout)")
    for item in changes:
        if item[0] == "added":
            print(f"  ADDED {item[1]}")
        else:
            print(f"  FROM {item[0]}")
            print(f"    TO {item[1]}")

    checks, _ = check_acceptance(new)
    print("\n===== ACCEPTANCE =====")
    all_ok = True
    for name, ok, detail in checks:
        all_ok = all_ok and ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} — {detail}")

    # numeric values: substituted rows must keep the same row_key nums (guaranteed by key match)
    nums_ok = all(row_key(b) == row_key(a) for b, a in substitutions)
    print(
        f"  [{'PASS' if nums_ok else 'FAIL'}] substitutions keep the same row_key numbers — {len(substitutions)} swaps"
    )
    all_ok = all_ok and nums_ok

    print("\n===== OLD merge_unique (for contrast) =====")
    for i, row in enumerate(old, 1):
        print(f"{i:02d}. {first_cell(row)}")

    print(f"\nEXPERIMENT {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
