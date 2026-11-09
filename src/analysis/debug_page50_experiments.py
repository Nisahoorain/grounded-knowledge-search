"""Isolated experiments A and B for page 50. Does not change production code.

A compares current is_amount/peel_amounts on parenthesized values.
B tries a local decimal split on the weighted-average leftovers only.
"""
import re
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import pdfplumber
from src.config import PDF_DIR
from src.ingestion.table_extractor import PEEL_AMOUNT_RE, is_amount, peel_amounts

PDF = PDF_DIR / "3M_2015_10K.pdf"
PAGE = 50
DECIMAL_RE = re.compile(r"\d+\.\d+")


def split_decimals(cell):
    """Local prototype. Two or more bare decimals leave the leading words in place."""
    found = list(DECIMAL_RE.finditer(cell))
    if len(found) < 2:
        return [cell]
    prefix = cell[: found[0].start()].strip(" —–-")
    out = [prefix] if prefix else []
    out.extend(m.group() for m in found)
    rest = cell[found[-1].end() :].strip()
    if rest:
        out.append(rest)
    return out


def experiment_a():
    tokens = ["(26)", "(33)", "(41)", "(1)", "(2)", "(3)", "(1,100)", "(2,200)"]
    print("=== Experiment A: current is_amount on parenthesized tokens ===")
    print(f"{'token':<12} {'is_amount':<10} footnote_rule")
    for token in tokens:
        footnote = bool(re.fullmatch(r"\(\d{1,2}\)", token.replace(" ", "")))
        print(f"{token:<12} {str(is_amount(token)):<10} {footnote}")

    contexts = [
        ["Interest income (26) (33) (41)"],
        ["See note (1)"],
        ["Revenue (1,100) (2,200)"],
        ["Interest income", "(26)", "(33)", "(41)"],
    ]
    print("\n=== Experiment A: current peel_amounts on context cells ===")
    for cells in contexts:
        print(f"  in:  {cells}")
        print(f"  out: {peel_amounts(cells)}")
        print(f"  is_amount: {[is_amount(c) for c in peel_amounts(cells)]}")

    small = [is_amount(t) for t in ["(26)", "(33)", "(41)", "(1)", "(2)", "(3)"]]
    large = [is_amount("(1,100)"), is_amount("(2,200)")]
    same_bucket = len(set(small)) == 1 and small[0] is False and all(large)
    print("\n=== Experiment A result ===")
    print(
        "1-2 digit parentheses, including (26) and (1), currently share one result:"
        f" is_amount={small[0]}"
    )
    print(f"(1,100) and (2,200) currently is_amount=True. same_bucket={same_bucket}")
    return same_bucket


def experiment_b():
    cases = {
        "basic 625.6 649.2 681.9": ["basic", "625.6", "649.2", "681.9"],
        "diluted 637.2 662.0 693.6": ["diluted", "637.2", "662.0", "693.6"],
    }
    unchanged = [
        "$ 7.72",
        "$ 7.58",
        "(26)",
        "(1)",
        "(1,100)",
        "30,274",
        "Total interest expense — net",
        "basic 625.6",
    ]
    print("\n=== Experiment B: current peel vs local decimal split ===")
    ok = True
    for text, expected in cases.items():
        current = peel_amounts([text])
        proposed = split_decimals(text)
        passed = proposed == expected
        ok = ok and passed
        print(f"  current peel: {current}")
        print(f"  proposed:     {proposed}")
        print(f"  expected:     {expected}  [{'PASS' if passed else 'FAIL'}]")

    print("\n=== Experiment B: cells the decimal split must leave unchanged ===")
    for text in unchanged:
        got = split_decimals(text)
        passed = got == [text]
        ok = ok and passed
        print(f"  [{'PASS' if passed else 'FAIL'}] {text!r} -> {got}")
    return ok


def page50_collateral():
    print("\n=== Experiment B collateral on page 50 layout cells ===")
    with pdfplumber.open(PDF) as pdf:
        text = pdf.pages[PAGE - 1].extract_text(layout=True) or ""
    changes = []
    for line in text.splitlines():
        parts = [p for p in re.split(r" {2,}", line.strip()) if p.strip()]
        if not parts:
            continue
        for cell in peel_amounts(parts):
            proposed = split_decimals(cell)
            if proposed != [cell]:
                changes.append((cell, proposed))
    if not changes:
        print("  (no cell changed)")
    for before, after in changes:
        print(f"  {before!r}")
        print(f"    -> {after}")
    only_shares = {tuple(after) for _, after in changes} == {
        ("basic", "625.6", "649.2", "681.9"),
        ("diluted", "637.2", "662.0", "693.6"),
    }
    print(f"  page 50 changes: {len(changes)}")
    print(f"  only the two share-count leftovers: {only_shares}")
    return only_shares


def peel_keep_gaps(cells):
    """Local prototype. Keep words that sit between peel matches."""
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
        cursor = found[0].start()
        for match in found:
            gap = cell[cursor : match.start()].strip()
            if gap:
                out.append(gap)
            out.append(match.group().strip())
            cursor = match.end()
        rest = cell[cursor:].strip()
        if rest:
            out.append(rest)
    return out


def experiment_c():
    basic = (
        "Earnings per share attributable to 3M Company shareholders — basic $ 7.72 $ 7.63 $ 6.83"
    )
    diluted = "diluted $ 7.58 $ 7.49 $ 6.72"
    expected = {
        basic: [
            "Earnings per share attributable to 3M Company shareholders",
            "—",
            "basic",
            "$ 7.72",
            "$ 7.63",
            "$ 6.83",
        ],
        diluted: ["diluted", "$ 7.58", "$ 7.49", "$ 6.72"],
    }
    print("=== Experiment C: keep text between peel matches ===")
    ok = True
    for text, want in expected.items():
        current = peel_amounts([text])
        proposed = peel_keep_gaps([text])
        passed = proposed == want
        ok = ok and passed
        print(f"  current:  {current}")
        print(f"  proposed: {proposed}")
        print(f"  expected: {want}  [{'PASS' if passed else 'FAIL'}]")

    print("\n=== Experiment C: actual page 50 EPS lines ===")
    with pdfplumber.open(PDF) as pdf:
        page_text = pdf.pages[PAGE - 1].extract_text(layout=True) or ""
    real_lines = [
        line.strip()
        for line in page_text.splitlines()
        if "shareholders" in line and ("basic" in line or "diluted" in line)
    ]
    for line in real_lines:
        current = peel_amounts([line])
        proposed = peel_keep_gaps([line])
        kept = "basic" in proposed or "diluted" in proposed
        ok = ok and kept and proposed != current
        print(f"  line:     {line}")
        print(f"  current:  {current}")
        print(f"  proposed: {proposed}  [{'PASS' if kept else 'FAIL'}]")

    print("\n=== Experiment C: other cells stay on current peel ===")
    stable = [
        "Total interest expense — net",
        "basic 625.6 649.2 681.9",
        "$ 7.72",
        "Interest income (26) (33) (41)",
        "Net sales $ 30,274 $ 31,821 $ 30,871",
    ]
    for text in stable:
        current = peel_amounts([text])
        proposed = peel_keep_gaps([text])
        passed = proposed == current
        ok = ok and passed
        print(f"  [{'PASS' if passed else 'FAIL'}] {text!r}")
        if not passed:
            print(f"    current  {current}")
            print(f"    proposed {proposed}")

    print("\n=== Experiment C collateral on page 50 ===")
    changes = []
    for line in page_text.splitlines():
        parts = [p for p in re.split(r" {2,}", line.strip()) if p.strip()]
        for cell in parts:
            current = peel_amounts([cell])
            proposed = peel_keep_gaps([cell])
            if proposed != current:
                changes.append((cell, current, proposed))
    for cell, current, proposed in changes:
        print(f"  {cell!r}")
        print(f"    current  {current}")
        print(f"    proposed {proposed}")
    only_eps = len(changes) == 2 and all(
        "shareholders" in cell and ("basic" in cell or "diluted" in cell)
        for cell, _, _ in changes
    )
    ok = ok and only_eps
    print(f"  page 50 changes: {len(changes)}")
    print(f"  only the two EPS lines: {only_eps}  [{'PASS' if only_eps else 'FAIL'}]")
    print("\n=== SUMMARY ===")
    print(f"  [{'PASS' if ok else 'FAIL'}] C preserves basic/diluted between the dash and the amounts")
    print("  production peel_amounts was not modified")
    return 0 if ok else 1


def main():
    a_ok = experiment_a()
    b_ok = experiment_b()
    collateral_ok = page50_collateral()
    print("\n=== SUMMARY ===")
    print(f"  [{'PASS' if a_ok else 'FAIL'}] A comparison: (26) and (1) are the same bucket; (1,100) is an amount")
    print(f"  [{'PASS' if b_ok else 'FAIL'}] B decimal split matches expected and leaves single values intact")
    print(f"  [{'PASS' if collateral_ok else 'FAIL'}] B on page 50 touches only the two weighted-average leftovers")
    print("  production peel_amounts and is_amount were not modified")
    return 0 if a_ok and b_ok and collateral_ok else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "C":
        sys.exit(experiment_c())
    sys.exit(main())
