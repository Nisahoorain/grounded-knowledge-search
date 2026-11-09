import logging
import re
import sys
from pathlib import Path

import pdfplumber

logging.getLogger("pdfminer").setLevel(logging.ERROR)

MIDWORD_RE = re.compile(r"[A-Za-z] \| [a-z]")
PROSE_RE = re.compile(
    r"\b(the following|information related|presented in the|refer to the|"
    r"table of contents|on an organic|sales in industrial totaled|"
    r"see accompanying)\b",
    re.I,
)


def _cells(row):
    out = []
    for c in row or []:
        if c is None:
            continue
        text = re.sub(r"\.{2,}", " ", str(c).replace("\n", " "))
        text = " ".join(text.split())
        if text:
            out.append(text)
    return out


def explode_dashes(cells):
    """Split a packed cell like '— — 25,290 — —' without touching normal '$ 4,869' cells."""
    out = []
    for cell in cells:
        if len(re.findall(r"[—–]", cell)) >= 2:
            out.extend(p for p in re.split(r"\s+", cell) if p)
        else:
            out.append(cell)
    return out


def real_amounts(cells):
    return sum(
        1
        for c in cells
        if is_amount(c) and c.replace(" ", "") not in {"$", "—", "–", "-"}
    )


def merge_units(cells):
    out = []
    i = 0
    while i < len(cells):
        cur, nxt = cells[i], cells[i + 1] if i + 1 < len(cells) else ""
        if cur == "$" and nxt:
            out.append("$ " + nxt)
            i += 2
            continue
        if nxt == "%" and cur:
            out.append(cur + "%")
            i += 2
            continue
        out.append(cur)
        i += 1
    return out


def is_amount(text):
    t = text.replace(" ", "")
    if t in {"$", "—", "–", "-"}:
        return True
    if re.fullmatch(r"\(\d{1,2}\)", t):
        return False
    return bool(re.fullmatch(r"\$?\(?-?[\d,]+(?:\.\d+)?\)?%?", t))


# thousands / $ / (123) / dashes — not dates like 30 or 2012
PEEL_AMOUNT_RE = re.compile(
    r"\$\s*\(?-?[\d,]+(?:\.\d+)?\)?"
    r"|\(\s*-?[\d,]+(?:\.\d+)?\s*\)"
    r"|-?[\d,]*\d,\d{3}(?:,\d{3})*(?:\.\d+)?"
    r"|[—–]"
)


def peel_amounts(cells):
    """Split a mixed label+numbers cell. Does not touch already-split '$ 4,869' rows."""
    out = []
    for cell in cells:
        found = list(PEEL_AMOUNT_RE.finditer(cell))
        if len(found) < 2 and not (
            len(found) == 1 and found[0].start() > 0 and re.search(r"[A-Za-z]", cell)
        ):
            out.append(cell)
            continue
        prefix = cell[: found[0].start()].strip()
        if prefix:
            out.append(prefix)
        out.extend(m.group().strip() for m in found)
        rest = cell[found[-1].end() :].strip()
        if rest:
            out.append(rest)
    return out


def split_second_label(cells):
    """Undo glued rows: '... (1,100,000) Equity awards assumed for acquisition'."""
    seen_amount = False
    for i, cell in enumerate(cells):
        if is_amount(cell) or PEEL_AMOUNT_RE.fullmatch(cell.replace(" ", "")):
            seen_amount = True
            continue
        if (
            seen_amount
            and i > 0
            and re.match(r"^[A-Z][A-Za-z]", cell)
            and len(cell.split()) >= 2
        ):
            return cells[:i], cells[i:]
    return cells, None


def is_shattered(cells):
    if MIDWORD_RE.search(" | ".join(cells)):
        return True
    for i, cell in enumerate(cells[:-1]):
        cur = cell.replace(" ", "")
        nxt = cells[i + 1].replace(" ", "")
        # '10,3' | '88'  or  '$1' | '0,399' / '017' — not '$61' | '3,038,665'
        if re.fullmatch(r"\d{1,2},\d{1,2}", cur) and nxt[:1].isdigit():
            return True
        if re.fullmatch(r"\$\d{1,2}", cur) and (
            nxt.startswith("0") or re.fullmatch(r"\d{3}", nxt)
        ):
            return True
        if re.fullmatch(r"\d", cur) and re.fullmatch(r"\d{3}", nxt):
            return True
    # only 1-letter fragments (charg | e); do not treat 'of' / 'in' / 'for' as shatter
    short = sum(1 for c in cells if re.fullmatch(r"[A-Za-z]", c))
    return len(cells) >= 4 and short >= 2


def is_prose(cells):
    text = " ".join(cells)
    if PROSE_RE.search(text):
        return True
    if real_amounts(cells) >= 2:
        return False
    if len(text) > 140:
        return True
    words = text.split()
    if "." in text and len(words) > 8:
        return True
    return len(words) > 10


def normalize(row):
    return merge_units(peel_amounts(explode_dashes(_cells(row))))


def keep_row(row):
    cells = normalize(row)
    if len(cells) < 2:
        return None
    if is_shattered(cells) or is_prose(cells):
        return None
    if real_amounts(cells) < 1:
        return None
    return cells


def label_only(cells):
    return " ".join(c for c in cells if not is_amount(c)).strip()


def attach_pending(pending, cells):
    if not pending or not cells:
        return cells
    first = cells[0]
    if is_amount(first):
        return [pending] + cells
    if first[:1].islower():
        return [pending + " " + first] + cells[1:]
    return cells


def emit_row(cells, pending, page_num):
    cells = attach_pending(pending, cells)
    kept = keep_row(cells)
    if not kept:
        return "", None
    return "", " | ".join(kept) + f" [Page: {page_num}]"


def serialize_rows(matrix, page_num):
    out = []
    pending = ""
    for row in matrix or []:
        cells = normalize(row)
        if not cells:
            continue
        first, second = split_second_label(cells)
        if real_amounts(first) < 1:
            label = label_only(first)
            if label and len(label) <= 90 and not label.endswith(":"):
                pending = (pending + " " + label).strip()
            cells = second or []
            if not cells:
                continue
            first, second = cells, None
        pending, line = emit_row(first, pending, page_num)
        if line:
            out.append(line)
        if second and real_amounts(second) >= 1:
            _, line = emit_row(second, "", page_num)
            if line:
                out.append(line)
        elif second:
            label = label_only(second)
            if label:
                pending = label
    return out


def layout_rows(page):
    try:
        text = page.extract_text(layout=True) or ""
    except TypeError:
        text = page.extract_text() or ""
    matrix = []
    for line in text.splitlines():
        line = re.sub(r"\.{2,}", "  ", line)
        parts = [p for p in re.split(r" {2,}", line.strip()) if p.strip()]
        if parts:
            matrix.append(parts)
    return serialize_rows(matrix, page.page_number)


def extract_method(page, settings):
    try:
        tables = page.extract_tables(table_settings=settings)
    except Exception:
        return []
    rows = []
    for table in tables or []:
        rows.extend(serialize_rows(table, page.page_number))
    return rows


def row_key(line):
    body = line.rsplit(" [Page:", 1)[0]
    label = re.sub(r"[^a-z]+", "", body.split("|")[0].lower())[:32]
    nums = tuple(re.findall(r"[\d,.]+", body))
    return (label, nums)


def row_quality(line):
    body = line.rsplit(" [Page:", 1)[0]
    cells = [c.strip() for c in body.split(" | ") if c.strip()]
    mashed = sum(1 for c in cells if len(re.findall(r"[—–]", c)) >= 2)
    return (real_amounts(cells), len(cells[0]) if cells else 0, -mashed)


def merge_unique(records):
    best = {}
    order = []
    for row in records:
        key = row_key(row)
        if key not in best:
            best[key] = row
            order.append(key)
            continue
        if row_quality(row) > row_quality(best[key]):
            best[key] = row
    return [best[k] for k in order]


def process_page(page):
    records = []
    records.extend(
        extract_method(
            page,
            {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
        )
    )
    records.extend(
        extract_method(
            page,
            {
                "vertical_strategy": "lines",
                "horizontal_strategy": "text",
                "snap_x_tolerance": 8,
                "snap_y_tolerance": 6,
            },
        )
    )
    records.extend(layout_rows(page))
    return merge_unique(records)


def extract_tables(pdf_path, doc_id=None):
    pdf_path = Path(pdf_path)
    if doc_id is None:
        doc_id = pdf_path.stem
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    extracted = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            rows = process_page(page)
            if not rows:
                continue
            extracted.append(
                {
                    "doc_id": doc_id,
                    "page": page.page_number,
                    "table_index": 1,
                    "content_type": "table",
                    "table_title": "",
                    "rows": [
                        [c.strip() for c in r.rsplit(" [Page:", 1)[0].split(" | ")]
                        for r in rows
                    ],
                    "text": "\n".join(rows),
                    "extraction_status": "success",
                    "extraction_method": "hybrid",
                }
            )
    return extracted


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python src/ingestion/table_extractor.py <pdf_path> [page]")
        sys.exit(1)
    tables = extract_tables(sys.argv[1])
    if len(sys.argv) >= 3:
        want = int(sys.argv[2])
        tables = [t for t in tables if t["page"] == want]
    print(f"tables: {len(tables)}")
    print(f"pages with tables: {sorted({t['page'] for t in tables})}")
    if tables:
        print(f"sample page: {tables[0]['page']}")
        print(tables[0]["text"])
