import csv
import json
import re
from collections import defaultdict
from pathlib import Path

from src.ingestion.chunker import _STATEMENT_HEADING
from src.config import EXTRACTED_DIR


def normalize_heading(line):
    readable = re.sub(r"\s+", " ", line).strip()

    # Same OCR repairs used by production chunker.
    readable = re.sub(r"(?i)\b(incom)\s+e\b", r"\1e", readable)
    readable = re.sub(r"(?i)\b(equit)\s+y\b", r"\1y", readable)
    readable = re.sub(r"(?i)\b(flow)\s+s\b", r"\1s", readable)
    readable = re.sub(r"(?i)\b(shee)\s+t\b", r"\1t", readable)

    return readable


def is_heading_candidate(line):
    compact = re.sub(r"\s+", "", line).lower()

    # Remove common statement qualifiers.
    compact = re.sub(
        r"^(?:(?:consolidated|condensed|combined))*",
        "",
        compact,
    )

    return compact.startswith((
        "statementof",
        "statementsof",
        "balancesheet",
        "incomestatement",
    ))


root = Path(EXTRACTED_DIR)

files = sorted(
    p for p in root.glob("*.json")
    if not p.name.startswith("_") and not p.name.endswith(".tmp")
)

results = defaultdict(lambda: {
    "count": 0,
    "matched": False,
    "examples": [],
})

total_pages = 0
total_candidates = 0
matched_lines = 0
missed_lines = 0

for path in files:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)

    for page in doc.get("pages") or []:
        total_pages += 1
        page_num = page.get("page", "?")

        for block in page.get("content") or []:
            if block.get("type") != "text":
                continue

            for raw_line in (block.get("text") or "").splitlines():
                original = raw_line.strip()

                if not original or len(original) > 180:
                    continue

                normalized = normalize_heading(original)

                if not is_heading_candidate(normalized):
                    continue

                total_candidates += 1
                matched = bool(_STATEMENT_HEADING.match(normalized))

                key = normalized
                item = results[key]
                item["count"] += 1
                item["matched"] = item["matched"] or matched

                if matched:
                    matched_lines += 1
                else:
                    missed_lines += 1

                example = f"{path.stem} | page {page_num}"

                if example not in item["examples"] and len(item["examples"]) < 3:
                    item["examples"].append(example)


print("=" * 75)
print("CORPUS-WIDE STATEMENT HEADING AUDIT")
print("=" * 75)
print(f"JSON files scanned:       {len(files)}")
print(f"Pages scanned:            {total_pages}")
print(f"Candidate heading lines:  {total_candidates}")
print(f"Regex matched lines:      {matched_lines}")
print(f"Regex missed lines:       {missed_lines}")
print(f"Unique heading variants:  {len(results)}")
print("=" * 75)

rows = []

for heading, item in sorted(
    results.items(),
    key=lambda x: (-x[1]["count"], x[0])
):
    status = "MATCHED" if item["matched"] else "MISSED"

    rows.append({
        "heading": heading,
        "count": item["count"],
        "status": status,
        "examples": " ; ".join(item["examples"]),
    })

    print(
        f"[{status:7}] {item['count']:4}x | {heading}"
    )

    for example in item["examples"]:
        print(f"           {example}")

output = Path("statement_heading_audit.csv")

with open(output, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=["heading", "count", "status", "examples"],
    )
    writer.writeheader()
    writer.writerows(rows)

print("=" * 75)
print(f"Full audit saved to: {output.resolve()}")
