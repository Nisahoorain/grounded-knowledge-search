"""Why layout_rows drops Common stock on 3M 2015 10-K page 52. Read-only."""
import re
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import pdfplumber
from src.config import PDF_DIR
import src.ingestion.table_extractor as te
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
    process_page,
    real_amounts,
    row_key,
    serialize_rows,
    split_second_label,
)

PDF = PDF_DIR / "3M_2015_10K.pdf"
PAGE = 52
NEEDLES = ("common stock", "shares outstanding", "shareholders", "paid-in", "treasury stock")


def main():
    with pdfplumber.open(PDF) as pdf:
        page = pdf.pages[PAGE - 1]
        text = page.extract_text(layout=True) or ""
        words = page.extract_words()

    print("=== layout lines that mention equity labels ===")
    matrix = []
    for i, line in enumerate(text.splitlines()):
        cleaned = re.sub(r"\.{2,}", "  ", line)
        parts = [p for p in re.split(r" {2,}", cleaned.strip()) if p.strip()]
        if parts:
            matrix.append(parts)
        low = line.lower()
        if any(n in low for n in NEEDLES):
            print(f"\nline {i}")
            print(repr(line))
            print("parts:", parts)
            cells = normalize(parts) if parts else []
            print("normalized:", cells)
            print(
                "real_amounts",
                real_amounts(cells),
                "prose",
                is_prose(cells) if cells else None,
                "shattered",
                is_shattered(cells) if cells else None,
                "keep",
                keep_row(parts) if parts else None,
            )
            if cells:
                first, second = split_second_label(cells)
                print("split_second_label first:", first)
                print("split_second_label second:", second)
                print("amounts in first:", real_amounts(first))

    print("\n=== serialize_rows output containing stock/share ===")
    for row in serialize_rows(matrix, PAGE):
        if any(n in row.lower() for n in ("stock", "share", "equity", "paid")):
            print(row)

    common = [w for w in words if w["text"] == "Common"]
    print("\n=== word positions on the Common stock line ===")
    if not common:
        print("no word 'Common'")
        return
    top = common[0]["top"]
    band = [w for w in words if abs(w["top"] - top) < 3]
    band.sort(key=lambda w: w["x0"])
    print(f"baseline top={top:.2f} words={len(band)}")
    prev_x1 = None
    for w in band:
        gap = None if prev_x1 is None else w["x0"] - prev_x1
        gap_s = "" if gap is None else f" gap={gap:.1f}"
        print(f"  x0={w['x0']:6.1f} x1={w['x1']:6.1f}{gap_s}  {w['text']!r}")
        prev_x1 = w["x1"]

    print("\n=== nearby lines by vertical position ===")
    # group words into visual lines
    lines = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if not lines or abs(w["top"] - lines[-1][0]["top"]) > 3:
            lines.append([w])
        else:
            lines[-1].append(w)
    for group in lines:
        label = " ".join(w["text"] for w in group)
        if any(n in label.lower() for n in NEEDLES) or "Equity" in label:
            tops = f"{group[0]['top']:.1f}"
            xs = f"{group[0]['x0']:.1f}-{group[-1]['x1']:.1f}"
            print(f"  y={tops} x={xs} | {label}")


GLUED_DOLLAR = re.compile(r"^\$\s+(.+?)\s+\$$")


def split_glued_dollar(cells):
    """Turn '$ 9 $' + '9' into '$ 9', '$ 9'. Does not rewrite merge_units."""
    out = []
    i = 0
    while i < len(cells):
        nxt = cells[i + 1] if i + 1 < len(cells) else ""
        match = GLUED_DOLLAR.fullmatch(cells[i])
        if match and nxt and is_amount(nxt) and not str(nxt).lstrip().startswith("$"):
            out.append("$ " + match.group(1).strip())
            out.append("$ " + nxt.strip())
            i += 2
            continue
        out.append(cells[i])
        i += 1
    return out


def normalize_with_cleanup(row):
    return split_glued_dollar(merge_units(peel_amounts(explode_dashes(_cells(row)))))


def body(row):
    return row.rsplit(" [Page:", 1)[0]


def first_cell(row):
    return body(row).split("|")[0].strip()


def experiment():
    with pdfplumber.open(PDF) as pdf:
        page = pdf.pages[PAGE - 1]
        text = page.extract_text(layout=True) or ""
        before_layout = te.layout_rows(page)
        before_page = process_page(page)

    print("\n=== pipeline stages for Common stock ===")
    for line in text.splitlines():
        if "Common stock" not in line:
            continue
        parts = [p for p in re.split(r" {2,}", line.strip()) if p.strip()]
        exploded = explode_dashes(_cells(parts))
        peeled = peel_amounts(exploded)
        merged = merge_units(peeled)
        cleaned = split_glued_dollar(merged)
        print("1 original parts:", parts)
        print("2 explode_dashes:", exploded)
        print("3 peel_amounts:  ", peeled)
        print("4 merge_units:   ", merged)
        print("5 cleanup:       ", cleaned)
        te.normalize = normalize_with_cleanup
        try:
            print("5 keep_row:      ", keep_row(parts))
            print("5 real_amounts:  ", real_amounts(cleaned), "prose", is_prose(cleaned))
        finally:
            te.normalize = normalize

    print("\n=== every merge_units cell matching '$ ... $' on this page ===")
    for line in text.splitlines():
        parts = [p for p in re.split(r" {2,}", line.strip()) if p.strip()]
        if not parts:
            continue
        merged = merge_units(peel_amounts(explode_dashes(_cells(parts))))
        if any(GLUED_DOLLAR.fullmatch(c) for c in merged):
            print(" before:", merged)
            print(" after: ", split_glued_dollar(merged))

    te.normalize = normalize_with_cleanup
    try:
        with pdfplumber.open(PDF) as pdf:
            page = pdf.pages[PAGE - 1]
            after_layout = te.layout_rows(page)
            after_page = process_page(page)
    finally:
        te.normalize = normalize

    def show(title, rows):
        print(f"\n=== {title} ({len(rows)}) ===")
        for i, row in enumerate(rows, 1):
            print(f"{i:02d}. {body(row)}")

    show("process_page BEFORE cleanup", before_page)
    show("process_page AFTER cleanup", after_page)

    before_labels = [first_cell(r) for r in before_page]
    after_labels = [first_cell(r) for r in after_page]
    i = 0
    order_ok = True
    for label in after_labels:
        if i < len(before_labels) and label == before_labels[i]:
            i += 1
    order_ok = i == len(before_labels)

    before_map = {body(r): r for r in before_page}
    changed = []
    for row in after_page:
        previous = before_map.get(body(row))
        if previous is None and body(row) not in {body(r) for r in before_page}:
            changed.append(("added", body(row)))
    before_bodies = {body(r) for r in before_page}
    after_bodies = {body(r) for r in after_page}
    for text_row in before_bodies - after_bodies:
        changed.append(("removed", text_row))
    for text_row in sorted(before_bodies & after_bodies):
        pass
    # same body text is unchanged; report label-matched numeric drift via row_key nums
    print("\n=== row text added or removed vs current production ===")
    if not changed:
        print("(none)")
    for kind, text_row in changed:
        print(f"  {kind}: {text_row}")

    def find_rows(rows, needle):
        return [body(r) for r in rows if needle in body(r).lower()]

    common = find_rows(after_page, "common stock, par value")
    shares_before = find_rows(before_page, "shares outstanding")
    shares_after = find_rows(after_page, "shares outstanding")
    paid_before = find_rows(before_page, "additional paid-in capital")
    paid_after = find_rows(after_page, "additional paid-in capital")
    keys = [row_key(r) for r in after_page]
    layout_keys = [row_key(r) for r in after_layout]
    before_layout_labels = [first_cell(r) for r in before_layout]
    after_layout_labels = [first_cell(r) for r in after_layout]
    j = 0
    layout_order_ok = True
    for label in after_layout_labels:
        if j < len(before_layout_labels) and label == before_layout_labels[j]:
            j += 1
    layout_order_ok = j == len(before_layout_labels)

    amounts_ok = False
    if len(common) == 1:
        cells = [c.strip() for c in common[0].split("|")]
        amounts_ok = cells[-2:] == ["$ 9", "$ 9"]

    checks = [
        ("Common stock row retained", len(common) == 1, common),
        ("2015 and 2014 amounts are $ 9 | $ 9", amounts_ok, common),
        ("Shares outstanding unchanged", shares_before == shares_after, shares_after),
        ("Additional paid-in capital unchanged", paid_before == paid_after, paid_after),
        ("Page 52 existing order preserved", order_ok, f"matched {i}/{len(before_labels)}"),
        ("Layout order preserved", layout_order_ok, f"matched {j}/{len(before_layout_labels)}"),
        ("Duplicate row keys", len(keys) == len(set(keys)), f"rows={len(keys)} unique={len(set(keys))}"),
        (
            "Layout duplicate keys",
            len(layout_keys) == len(set(layout_keys)),
            f"rows={len(layout_keys)} unique={len(set(layout_keys))}",
        ),
    ]
    print("\n=== ACCEPTANCE ===")
    all_ok = True
    for name, ok, detail in checks:
        all_ok = all_ok and ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} — {detail}")
    print(f"\nEXPERIMENT {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    main()
    sys.exit(experiment())
