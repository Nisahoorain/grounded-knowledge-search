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
CHUNKING_VERSION = "table-row-v4"
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

_PAGE_TAG = re.compile(r"\s*\[Page:\s*\d+\]\s*$")
_BARE_YEAR = re.compile(r"^(?:19|20)\d{2}$")
_MONTH = (
    r"(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december)"
)
_FULL_DATE = re.compile(rf"(?i)^{_MONTH}\s+\d{{1,2}},\s*(?:19|20)\d{{2}}$")
_DATE_STUB = re.compile(rf"(?i)^(?:year ended\s+)?{_MONTH}\s+\d{{1,2}},?$")
_AMOUNT_LINE = re.compile(r"^\$?\(?-?[\d,]+(?:\.\d+)?\)?%?$|^[—–\-]$|^\$[—–\-]$")
# Statement sections that introduce the rows under them. A line is a section
# only when the next line is another label, not a number.
_SECTIONS = {
    "assets",
    "current assets",
    "noncurrent assets",
    "non-current assets",
    "other assets",
    "other long-term assets",
    "property and equipment",
    "property, plant and equipment",
    "liabilities",
    "liabilities and stockholders' equity",
    "liabilities and shareholders' equity",
    "liabilities and stockholders equity",
    "liabilities and equity",
    "current liabilities",
    "noncurrent liabilities",
    "non-current liabilities",
    "long-term liabilities",
    "other liabilities",
    "stockholders' equity",
    "shareholders' equity",
    "stockholders equity",
    "equity",
    "revenues",
    "revenue",
    "net revenues",
    "net revenue",
    "net sales",
    "expenses",
    "operating expenses",
    "costs and expenses",
    "cost of sales",
    "cost of revenue",
    "cost of revenues",
    "cost of products sold",
    "other income (expense)",
    "other income",
    "non-operating income (expense)",
    "nonoperating income (expense)",
    "earnings per share",
    "net income per share",
    "weighted average common shares outstanding",
    "weighted average shares outstanding",
    "cash flows from operating activities",
    "cash flows from investing activities",
    "cash flows from financing activities",
    "operating activities",
    "investing activities",
    "financing activities",
}


def _norm(text):
    text = (
        (text or "")
        .replace("\xa0", " ")
        .replace("\u2019", "'")
        .replace("\u2018", "'")
    )
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text.rstrip(":").strip()


def _page_lines(page):
    lines = []
    for block in page.get("content") or []:
        if block.get("type") != "text":
            continue
        for line in (block.get("text") or "").splitlines():
            clean = re.sub(r"[ \t]+", " ", line.replace("\xa0", " ")).strip()
            if clean:
                lines.append(clean)
    return lines


def _amountish(line):
    token = _norm(line).replace(" ", "").replace("$", "")
    if token in {"", "-", "—", "–"}:
        return True
    if _BARE_YEAR.match(_norm(line)):
        return False
    return bool(_AMOUNT_LINE.fullmatch(token))


def _is_section_at(lines, index):
    if _norm(lines[index]) not in _SECTIONS:
        return False
    for nxt in lines[index + 1 : index + 5]:
        if _amountish(nxt):
            return False
        if re.search(r"[a-z]", _norm(nxt)):
            return True
    return False


def _display_section(line):
    return re.sub(r"\s+", " ", line).strip().rstrip(":").strip()


def _closes_section(line, section):
    if not section:
        return False
    name = _norm(line)
    current = _norm(section)
    return name == f"total {current}" or name.startswith(f"total {current} ")


def _label_match(line, label):
    if len(label) < 4:
        return False
    name = _norm(line)
    if name == label:
        return True
    if len(name) >= 12 and label.startswith(name) and len(label) - len(name) <= 40:
        return True
    if name.startswith(label) and 0 < len(name) - len(label) <= 20:
        return True
    return False


_UNIT_LINE = re.compile(r"(?i)\b(?:in thousands|in millions|in billions)\b")


def _printed_unit(lines):
    """The denomination line printed above a statement, when the page has one."""
    for line in lines[:20]:
        if _UNIT_LINE.search(line) and len(line) <= 90:
            return re.sub(r"\s+", " ", line).strip()
    return ""


def _page_columns(lines):
    """Column headings above the first data row, in left-to-right order."""
    columns = []
    pending = ""
    for index, line in enumerate(lines[:40]):
        name = _norm(line)
        if _FULL_DATE.match(name):
            columns.append(re.sub(r"\s+", " ", line).strip())
            pending = ""
            continue
        if _DATE_STUB.match(name):
            pending = re.sub(r"(?i)^year ended\s+", "", name).rstrip(",").strip().title()
            continue
        if _is_section_at(lines, index):
            if len(columns) >= 2:
                break
            pending = ""
            continue
        if _BARE_YEAR.match(name):
            columns.append(f"{pending}, {name}" if pending else name)
            continue
        if columns:
            break
        pending = ""
    return columns if len(columns) >= 2 else []


def _bare_years(columns):
    years = []
    for column in columns:
        found = _YEAR_HEADER.findall(column)
        if found:
            years.append(found[-1])
    return years if len(years) >= 2 else []


def _row_cells(line):
    body = _PAGE_TAG.sub("", line).strip()
    return [cell.strip() for cell in body.split(" | ") if cell.strip()]


def _row_label(line):
    cells = _row_cells(line)
    if not cells or not re.search(r"[A-Za-z]", cells[0]):
        return ""
    return cells[0]


def _bare_year_cells(line):
    return [cell for cell in _row_cells(line) if _BARE_YEAR.match(cell)]


def _align_sections(lines, labels):
    """Section heading in force at each row. Blank when the page does not align."""
    current = ""
    pos = 0
    sections = []
    found = 0
    labeled = 0
    for label in labels:
        norm_label = _norm(label)
        if len(norm_label) < 4 or not re.search(r"[a-z]", norm_label):
            sections.append(current)
            continue
        labeled += 1
        walking = current
        match_at = None
        for index in range(pos, len(lines)):
            if _is_section_at(lines, index):
                walking = _display_section(lines[index])
                continue
            if _label_match(lines[index], norm_label):
                match_at = index
                break
        if match_at is None:
            sections.append("")
            continue
        current = walking
        sections.append(current)
        found += 1
        if _closes_section(lines[match_at], current):
            current = ""
        pos = match_at + 1
    confident = labeled >= 3 and found / labeled >= 0.5
    if not confident:
        return [""] * len(labels)
    return sections


def _identity(doc):
    company = str(doc.get("company") or "").strip()
    period = str(doc.get("period") or "").strip()
    doc_id = str(doc.get("doc_id") or "").strip()
    parts = []
    if company:
        parts.append(f"Company: {company}")
    if period:
        parts.append(f"Year: {period}")
    if doc_id:
        parts.append(f"Document: {doc_id}")
    return " | ".join(parts)


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
    return [piece for _section, _columns, piece in _table_row_pieces(text, [], [])]


def _table_body(text):
    rows = [row.strip() for row in text.splitlines() if row.strip()]
    if not rows:
        return "", []
    header = rows[0] if len(_YEAR_HEADER.findall(rows[0])) >= 2 else ""
    body = rows[1:] if header else rows
    if not body:
        return "", [header] if header else []
    return header, body


def _table_row_pieces(text, sections, columns):
    """Repeat the year header and the section in force on every data row.

    The year line sits directly above the row so a later reader can zip
    column years to amounts. ``columns`` is the printed heading, which may
    name a fiscal date rather than only the year.
    """
    header, body = _table_body(text)
    if not body:
        return []
    if len(sections) < len(body):
        sections = list(sections) + [""] * (len(body) - len(sections))
    bare = _bare_years(columns)
    header_is_years = len(_bare_year_cells(header)) >= 2
    year_line = header if header_is_years else (" | ".join(bare) if len(bare) >= 2 else "")
    carried = header if header and not header_is_years else ""
    column_line = "Columns: " + " | ".join(columns) if len(columns) >= 2 else ""
    local = ""
    pieces = []
    for row, section in zip(body, sections):
        label = _row_label(row)
        if _norm(label) in _SECTIONS and len(_row_cells(row)) < 2:
            local = _display_section(label)
            continue
        active = local or section
        lines = []
        if column_line:
            lines.append(column_line)
        if active:
            lines.append("Section: " + active)
        if carried:
            lines.append(carried)
        if year_line:
            lines.append(year_line)
        lines.append(row)
        pieces.append((active, column_line[len("Columns: ") :] if column_line else "", "\n".join(lines)))
        if _closes_section(label, active):
            local = ""
    return pieces


def _chunks_for_block(
    doc,
    page_num,
    content_type,
    text,
    size,
    overlap,
    page_context="",
    page_lines=None,
):
    text = (text or "").strip()
    if not text:
        return []
    identity = _identity(doc)
    statement = (page_context or "").strip()
    meta = {key: doc.get(key, "") for key in META_FIELDS}
    records = []
    if content_type == "table":
        header, body = _table_body(text)
        labels = [_row_label(row) for row in body]
        sections = _align_sections(page_lines or [], labels) if page_lines else []
        columns = _page_columns(page_lines or []) if sections and any(sections) else []
        if not columns and len(_bare_year_cells(header)) >= 2:
            columns = _bare_year_cells(header)
        unit = _printed_unit(page_lines or []) if (statement or columns) else ""
        for section, column_text, piece in _table_row_pieces(text, sections, columns):
            prefix = [line for line in (identity, statement, unit) if line]
            full = "\n".join(prefix + [piece]) if prefix else piece
            records.append((full, statement, section, column_text))
    else:
        pieces = split_words(text, size, overlap)
        for piece in pieces:
            full = f"{identity}\n{piece}" if identity else piece
            records.append((full, "", "", ""))
    out = []
    for index, (piece, statement_name, section, column_text) in enumerate(records):
        out.append(
            {
                **meta,
                "page": page_num,
                "content_type": content_type,
                "chunk_index": index,
                "chunk_id": chunk_id(meta["doc_id"], page_num, content_type, index, piece),
                "text": piece,
                "n_tokens": approx_tokens(piece),
                "statement": statement_name,
                "section": section,
                "columns": column_text,
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
        lines = _page_lines(page)
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
                    page_lines=lines,
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
