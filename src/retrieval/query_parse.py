import csv
import re
from functools import lru_cache
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[2] / "manifest.csv"

# key = doc_id ka prefix (year se pehle ka hissa)
ALIASES = {
    "PEPSICO": ["pepsico", "pepsi"],
    "BESTBUY": ["best buy", "bestbuy"],
    "MGMRESORTS": ["mgm resorts", "mgm"],
    "ULTABEAUTY": ["ulta beauty", "ulta"],
    "JOHNSON_JOHNSON": ["johnson & johnson", "johnson and johnson", "j&j", "jnj"],
    "PFIZER": ["pfizer"],
    "3M": ["3m"],
    "BOEING": ["boeing"],
    "AMD": ["amd", "advanced micro devices"],
    "ADOBE": ["adobe"],
    "CORNING": ["corning"],
    "VERIZON": ["verizon"],
}

# \b sits before "fy" so "FY2022" matches. A boundary between "fy" and "2022"
# never fires: both are word characters.
YEAR_RE = re.compile(r"\b(?:fy\s?)?((?:19|20)\d{2})\b", re.I)
FY2_RE = re.compile(r"\bfy\s?(\d{2})\b", re.I)

TYPE_PATTERNS = {
    "10k": r"\b10-?k\b",
    "10q": r"\b10-?q\b|\bquarterly report\b",
    "8k": r"\b8-?k\b",
    "earnings": r"\bearnings (release|report|call)\b|\bpress release\b",
    "annual": r"\bannual report\b",
}


def _dtype(doc_id):
    u = doc_id.upper()
    if "EARNINGS" in u:
        return "earnings"
    if "ANNUALREPORT" in u or "ANNUAL_REPORT" in u:
        return "annual"
    if "_10Q" in u:
        return "10q"
    if "_8K" in u:
        return "8k"
    if "_10K" in u:
        return "10k"
    return "other"


@lru_cache(maxsize=1)
def _manifest():
    rows = []
    with open(MANIFEST, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            m = re.match(r"(.+?)_(\d{4})", r["doc_id"])
            if not m:
                continue
            rows.append({
                "doc_id": r["doc_id"],
                "prefix": m.group(1),
                "year": int(m.group(2)),
                "desk": r["desk"],
                "type": _dtype(r["doc_id"]),
            })
    return rows


def parse_query(question):
    q = question.lower()
    prefixes, clean = set(), q

    for prefix, aliases in ALIASES.items():
        for a in sorted(aliases, key=len, reverse=True):
            pat = r"(?<![a-z0-9])" + re.escape(a) + r"(?![a-z0-9])"
            if re.search(pat, clean):
                prefixes.add(prefix)
                clean = re.sub(pat, " ", clean)

    years = {int(y) for y in YEAR_RE.findall(q)}
    clean = YEAR_RE.sub(" ", clean)
    years |= {2000 + int(y) for y in FY2_RE.findall(q)}
    clean = FY2_RE.sub(" ", clean)

    docs = []
    if prefixes:
        docs = [d for d in _manifest() if d["prefix"] in prefixes]
        if years:
            by_year = [d for d in docs if d["year"] in years]
            if not by_year:
                earlier = [d for d in docs if d["year"] < min(years)]
                if earlier:
                    latest = max(d["year"] for d in earlier)
                    by_year = [d for d in earlier if d["year"] == latest]
            docs = by_year or docs

    types = {t for t, pat in TYPE_PATTERNS.items() if re.search(pat, q)}
    if types and docs:
        typed = [d for d in docs if d["type"] in types]
        docs = typed or docs

    return {
        "companies": prefixes,
        "years": years,
        "types": types,
        "docs": docs,   # list of {doc_id, desk, type, ...}
        "clean_question": " ".join(clean.split()),
    }