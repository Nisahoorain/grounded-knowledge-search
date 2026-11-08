import re
import sys
from pathlib import Path

import pdfplumber

AMOUNT_RE = re.compile(r"^\$?\(?-?[\d,]+\)?%?$")


def _clean_cell(cell):
    if cell is None:
        return ""
    return " ".join(str(cell).split())


def _pad_rows(rows):
    width = max((len(row) for row in rows), default=0)
    return [row + [""] * (width - len(row)) for row in rows]


def drop_empty_rows_and_cols(rows):
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return rows

    rows = _pad_rows(rows)
    keep = [
        col
        for col in range(len(rows[0]))
        if any(row[col].strip() for row in rows)
    ]
    return [[row[col] for col in keep] for row in rows]


def merge_dollar_columns(rows):
    if not rows:
        return rows

    rows = _pad_rows(rows)
    cleaned_rows = []

    for row in rows:
        new_row = []
        i = 0

        while i < len(row):
            cell = row[i].strip()

            if cell == "$" and i + 1 < len(row):
                value = row[i + 1].strip()
                new_row.append(f"$ {value}" if value else "$")
                i += 2
            else:
                if cell:
                    new_row.append(cell)
                i += 1

        cleaned_rows.append(new_row)

    return cleaned_rows

    
def clean_table(table):
    rows = [[_clean_cell(cell) for cell in row] for row in table]

    # Remove completely empty rows
    rows = [row for row in rows if any(row)]

    if not rows:
        return []

    # Remove completely empty columns
    rows = _pad_rows(rows)
    keep = [
        col for col in range(len(rows[0]))
        if any(row[col] for row in rows)
    ]
    rows = [[row[col] for col in keep] for row in rows]

    # Merge dollar signs with their corresponding values
    rows = merge_dollar_columns(rows)

    # Final cleanup
    rows = drop_empty_rows_and_cols(rows)

    return rows


def is_junk_table(rows):
    if not rows:
        return True
    if len(rows) == 1 and len(rows[0]) <= 1:
        return True
    return False


def page_tables_are_fragmented(tables):
    """Default extract_tables() sometimes splits one statement into many tiny tables."""
    if len(tables) < 4:
        return False
    avg_rows = sum(len(table) for table in tables) / len(tables)
    return len(tables) >= 8 or avg_rows <= 3


def page_text_as_table(page):
    text = page.extract_text() or ""
    lines = []
    for line in text.splitlines():
        if line.strip().lower() == "table of contents":
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def is_amount_token(text):
    text = text.strip().replace("\xa0", "")
    if text in {"$", "—", "–", "-"}:
        return True
    return bool(AMOUNT_RE.match(text))


def group_words_into_lines(words, y_tol=4):
    words = sorted(words, key=lambda word: (word["top"], word["x0"]))
    lines = []
    for word in words:
        if word["text"].strip().lower() == "table of contents":
            continue
        if lines and abs(word["top"] - lines[-1]["top"]) <= y_tol:
            lines[-1]["words"].append(word)
        else:
            lines.append({"top": word["top"], "words": [word]})
    return lines


def merge_dollar_words(words):
    words = sorted(words, key=lambda word: word["x0"])
    merged = []
    index = 0
    while index < len(words):
        word = words[index]
        nxt = words[index + 1] if index + 1 < len(words) else None
        nxt_text = nxt["text"].strip() if nxt else ""
        if (
            word["text"].strip() == "$"
            and nxt is not None
            and AMOUNT_RE.match(nxt_text.replace(" ", ""))
        ):
            # keep the amount's X so $1,798 and $1,897 land in different columns
            merged.append({
                "text": "$ " + nxt["text"],
                "x0": nxt["x0"],
                "top": nxt["top"],
            })
            index += 2
        else:
            merged.append(word)
            index += 1
    return merged


def cluster_x_positions(xs, x_tol=25):
    if not xs:
        return []
    xs = sorted(xs)
    clusters = [[xs[0]]]
    for x in xs[1:]:
        if x - clusters[-1][-1] <= x_tol:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    return [sum(cluster) / len(cluster) for cluster in clusters]


def nearest_col(x, col_xs):
    return min(range(len(col_xs)), key=lambda i: abs(x - col_xs[i]))


def _compact_token(text):
    return text.strip().replace("\xa0", "").replace(" ", "")


def _column_value_kind(token):
    compact = _compact_token(token)
    if compact in {"—", "–", "-"}:
        return "blank"
    if compact == "$":
        return None
    # Note 14) / $91 in a label — not year-column amounts
    if re.match(r"^\d{1,2}\)$", compact):
        return None
    if re.match(r"^\$\d{1,3}$", compact):
        return None
    if AMOUNT_RE.match(compact):
        return "amount"
    if compact.startswith("$") and AMOUNT_RE.match(compact[1:]):
        return "amount"
    return None


def _right_column_pack(col_xs, min_gap=80):
    """Keep the year columns on the right; drop leftover left clusters ($91, etc.)."""
    col_xs = sorted(col_xs)
    if len(col_xs) < 2:
        return col_xs
    split_at = 0
    for i in range(1, len(col_xs)):
        if col_xs[i] - col_xs[i - 1] > min_gap:
            split_at = i
    return col_xs[split_at:]


def _is_header_or_footer_row(cells):
    label = cells[0].strip().lower() if cells else ""
    values = [cell.strip() for cell in cells[1:] if cell.strip()]
    if label == "table of contents":
        return True
    if label.isdigit() and len(label) <= 3 and not values:
        return True
    return False


def reconstruct_rows_from_words(page):
    words = page.extract_words() or []
    if not words:
        return []

    prepared_lines = []
    amount_xs = []
    snap = 30

    for line in group_words_into_lines(words):
        merged = merge_dollar_words(line["words"])
        prepared_lines.append(merged)
        for word in merged:
            if _column_value_kind(word["text"]) == "amount":
                amount_xs.append(word["x0"])

    raw_cols = cluster_x_positions(amount_xs, x_tol=30)
    scored = []
    for col_x in raw_cols:
        count = sum(1 for x in amount_xs if abs(x - col_x) <= snap)
        if count >= 3:
            scored.append(col_x)
    col_xs = scored[-4:] if scored else raw_cols[-4:]
    col_xs = _right_column_pack(col_xs)
    if not col_xs:
        return []

    rows = []
    for merged in prepared_lines:
        cells = [""] * (1 + len(col_xs))
        label_parts = []
        for index, word in enumerate(merged):
            token = word["text"].strip()
            if not token:
                continue
            kind = _column_value_kind(token)
            col = nearest_col(word["x0"], col_xs)
            close_to_col = abs(word["x0"] - col_xs[col]) <= snap
            nxt = None
            for later in merged[index + 1:]:
                if later["text"].strip():
                    nxt = later["text"].strip()
                    break
            # "— net" is a label hyphen, not a missing 2015 value
            if kind == "blank" and nxt and _column_value_kind(nxt) is None:
                label_parts.append(token)
                continue
            if kind and close_to_col:
                cells[col + 1] = (cells[col + 1] + " " + token).strip()
            else:
                label_parts.append(token)
        cells[0] = " ".join(label_parts)
        if any(cell.strip() for cell in cells) and not _is_header_or_footer_row(cells):
            rows.append(cells)

    return drop_empty_rows_and_cols(rows)


def to_searchable_text(rows):
    lines = []
    for row in rows:
        label = row[0] if row else ""
        values = [cell for cell in row[1:] if cell]
        if values:
            lines.append(f"{label} | " + " | ".join(values))
        elif label:
            lines.append(label)
    return "\n".join(lines)


def extract_tables(pdf_path, doc_id=None):
    """Extract tables page by page. Page numbers are 1-based."""
    pdf_path = Path(pdf_path)

    if doc_id is None:
        doc_id = pdf_path.stem

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    try:
        pdf = pdfplumber.open(pdf_path)
    except Exception as exc:
        raise RuntimeError(f"Could not open PDF {pdf_path}: {exc}") from exc

    extracted = []

    try:
        for page in pdf.pages:
            page_number = page.page_number
            extraction_status = "success"
            try:
                raw_tables = page.extract_tables() or []
            except Exception as exc:
                extraction_status = "failed"
                print(
                    f"tables failed on page {page_number} in {doc_id}: {exc}",
                    file=sys.stderr,
                )
                extracted.append({
                    "doc_id": doc_id,
                    "page": page_number,
                    "table_index": None,
                    "content_type": "table",
                    "table_title": "",
                    "rows": [],
                    "text": "",
                    "extraction_status": extraction_status,
                    "extraction_method": "pdfplumber",
                })
                continue

            cleaned_tables = []
            for table_index, table in enumerate(raw_tables, start=1):
                if not table:
                    continue
                rows = clean_table(table)
                if is_junk_table(rows):
                    continue
                cleaned_tables.append((table_index, rows))

            if page_tables_are_fragmented([rows for _, rows in cleaned_tables]):
                rows = reconstruct_rows_from_words(page)
                page_text = page_text_as_table(page)
                if rows:
                    searchable = to_searchable_text(rows)
                else:
                    searchable = page_text
                extracted.append({
                    "doc_id": doc_id,
                    "page": page_number,
                    "table_index": 1,
                    "content_type": "table",
                    "table_title": "",
                    "rows": rows,
                    "text": searchable,
                    "extraction_status": "success",
                    "extraction_method": "word_coords",
                })
                continue

            for table_index, rows in cleaned_tables:
                extracted.append({
                    "doc_id": doc_id,
                    "page": page_number,
                    "table_index": table_index,
                    "content_type": "table",
                    "table_title": "",
                    "rows": rows,
                    "text": to_searchable_text(rows),
                    "extraction_status": "success",
                    "extraction_method": "pdfplumber",
                })
    finally:
        pdf.close()

    return extracted


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python src/ingestion/table_extractor.py <pdf_path>")
        sys.exit(1)

    tables = extract_tables(sys.argv[1])
    failed = [t["page"] for t in tables if t["extraction_status"] == "failed"]
    pages = sorted({t["page"] for t in tables})

    print(f"tables: {len(tables)}")
    print(f"pages with tables: {pages}")
    print(f"failed pages: {failed if failed else 0}")

    sample_page = 3 if 3 in pages else (pages[0] if pages else None)
    if sample_page is None:
        print("no tables found")
        sys.exit(0)

    samples = [t for t in tables if t["page"] == sample_page]
    print(f"\nsample page {sample_page}: {len(samples)} table(s)")
    first = samples[0]
    print(f"doc_id: {first['doc_id']}")
    print(f"table_index: {first['table_index']}")
    print(f"table_title: {first['table_title']}")
    print(f"extraction_status: {first['extraction_status']}")
    print(f"extraction_method: {first['extraction_method']}")
    print(first["text"][:800])
