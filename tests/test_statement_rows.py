import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.answering.formula_eval import answer_from_formula, answer_from_reported_line
from src.retrieval.statement_rows import (
    attach_statement_rows,
    choose_row,
    line_items_in,
    statements_in_question,
    without_printed_metric,
)

DPO = (
    "Based on the information provided primarily in the balance sheet and the "
    "statement of income, what is FY2020 days payable outstanding (DPO) for Corning? "
    "DPO is defined as: 365 * (average accounts payable between FY2019 and FY2020) / "
    "(FY2020 COGS + change in inventory between FY2019 and FY2020). "
    "Round your answer to two decimal places."
)

COGS = {
    "phrase": "cogs",
    "queries": ("cost of sales",),
    "home": "income",
    "aliases": ("cost of sales", "cost of goods"),
}
INVENTORY = {
    "phrase": "inventory",
    "queries": ("inventories", "inventory"),
    "home": "balance_sheet",
    "aliases": ("inventories", "inventory"),
}
PAYABLE = {
    "phrase": "accounts payable",
    "queries": ("accounts payable",),
    "home": "balance_sheet",
    "aliases": ("accounts payable",),
}


def _hit(chunk_id, page, text):
    return {"chunk_id": chunk_id, "doc_id": "CORNING_2020_10K", "page": page, "text": text}


class DetectionTests(unittest.TestCase):
    def test_formula_question_names_its_lines_for_any_company(self):
        phrases = {item["phrase"] for item in line_items_in(DPO)}
        self.assertEqual(phrases, {"accounts payable", "inventory", "cogs"})
        self.assertEqual(statements_in_question(DPO), {"balance_sheet", "income"})

    def test_single_balance_sheet_line_is_included(self):
        question = (
            "Basing your judgments off of the balance sheet, what is the year end "
            "FY2018 amount of accounts payable for MGM Resorts?"
        )
        phrases = [item["phrase"] for item in line_items_in(question)]
        self.assertEqual(phrases, ["accounts payable"])

    def test_margin_question_adds_income_statement_parts(self):
        question = (
            "In agreement with the information outlined in the income statement, "
            "what is the FY2015 - FY2017 3 year average net profit margin for Best Buy?"
        )
        phrases = {item["phrase"] for item in line_items_in(question)}
        self.assertEqual(phrases, {"net income", "net sales"})

    def test_a_question_with_no_line_is_left_alone(self):
        question = "Has Pepsico reported any materially important ongoing legal battles from FY2022?"
        self.assertEqual(line_items_in(question), [])

    def test_one_unnamed_line_does_not_trigger(self):
        question = "What were Pfizer's total revenues for FY2022?"
        self.assertEqual(line_items_in(question), [])


class SelectionTests(unittest.TestCase):
    def test_income_statement_row_beats_a_footnote_with_the_same_label(self):
        hits = [
            _hit("note", 115, "Columns: 2020 | 2019 | 2018\nCost of sales | 13 | 11 | 13"),
            _hit(
                "stmt",
                70,
                "Consolidated Statements of Income\nColumns: 2020 | 2019 | 2018\n"
                "Cost of sales | 7,772 | 7,468 | 6,829",
            ),
        ]
        chosen = choose_row(hits, COGS, {"balance_sheet", "income"})
        self.assertEqual(chosen["page"], 70)

    def test_balance_sheet_inventory_beats_the_summary_and_a_pension_note(self):
        hits = [
            _hit("pension", 105, "Projected benefit obligation 4,100 and 3,900"),
            _hit(
                "summary",
                43,
                "Summary of cash flow data\nInventories | 2,438 | 2,320\n"
                "Days payable outstanding | 44 | 48",
            ),
            _hit(
                "sheet",
                72,
                "Consolidated Balance Sheets\nColumns: 2020 | 2019\n"
                "Inventories, net (Note 6) | 2,438 | 2,320",
            ),
        ]
        chosen = choose_row(hits, INVENTORY, {"balance_sheet", "income"})
        self.assertEqual(chosen["page"], 72)

    def test_same_page_can_contribute_two_lines(self):
        payable = _hit(
            "ap",
            72,
            "Consolidated Balance Sheets\nAccounts payable | 1,174 | 1,587",
        )
        inventory = _hit(
            "inv",
            72,
            "Consolidated Balance Sheets\nInventories, net | 2,438 | 2,320",
        )
        cogs = _hit(
            "cogs",
            70,
            "Consolidated Statements of Income\nCost of sales | 7,772 | 7,468 | 6,829",
        )

        def search(query, role, k, doc_ids):
            return {
                "accounts payable": [payable],
                "inventories": [inventory],
                "inventory": [inventory],
                "cost of sales": [cogs],
            }[query]

        merged = attach_statement_rows(DPO, "tech_telecom", [], search=search)
        pages = sorted((row["page"], row["line_item"]) for row in merged)
        self.assertEqual(pages, [(70, "cogs"), (72, "accounts payable"), (72, "inventory")])

    def test_parent_statement_beats_a_subsidiary_row(self):
        item = dict(PAYABLE)
        hits = [
            _hit(
                "exhibit",
                174,
                "CONSOLIDATED BALANCE SHEETS\nAccounts payable | 25,758 | 24,950",
            ),
            _hit(
                "parent",
                57,
                "MGM RESORTS INTERNATIONAL AND SUBSIDIARIES\n"
                "CONSOLIDATED BALANCE SHEETS\nAccounts payable | 302,578 | 255,028",
            ),
        ]
        hits[0]["doc_id"] = "MGMRESORTS_2018_10K"
        hits[1]["doc_id"] = "MGMRESORTS_2018_10K"
        chosen = choose_row(hits, item, {"balance_sheet"})
        self.assertEqual(chosen["page"], 57)

    def test_a_page_number_does_not_turn_an_index_line_into_a_row(self):
        hits = [
            _hit("index", 75, "Property, plant and equipment | 36 [Page: 75]"),
            _hit(
                "stmt",
                52,
                "Consolidated Statements of Financial Position\n"
                "Property, plant and equipment, net | 12,645 | 12,672",
            ),
        ]
        item = {
            "phrase": "property, plant, and equipment",
            "home": "balance_sheet",
            "aliases": ("property plant and equipment", "property plant"),
        }
        chosen = choose_row(hits, item, {"balance_sheet"})
        self.assertEqual(chosen["page"], 52)

    def test_ranked_results_stay_and_the_missing_line_is_added(self):
        ranked = [_hit("other", 43, "Days payable outstanding | 44 | 48")]
        extra = _hit(
            "ap",
            72,
            "Consolidated Balance Sheets\nAccounts payable | 1,174 | 1,587",
        )

        def search(query, role, k, doc_ids):
            if query == "accounts payable":
                return [extra]
            return []

        question = (
            "From the balance sheet, what is Corning accounts payable and "
            "what is inventory for FY2020?"
        )
        merged = attach_statement_rows(question, "tech_telecom", ranked, search=search)
        self.assertEqual([row["chunk_id"] for row in merged], ["other", "ap"])


class LooseStatementTests(unittest.TestCase):
    def test_operations_text_beats_an_attributable_summary(self):
        operations = _hit(
            "ops",
            56,
            "Consolidated Statements of Operations\n"
            "Net loss\n$\n(660)  $\n(403)  $\n(83)\n",
        )
        summary = _hit(
            "summary",
            34,
            "Net income (loss) attributable to AMD common stockholders | $ (660) | $ (403) | $ (83)",
        )
        equity = _hit(
            "equity",
            59,
            "Consolidated Statements of Stockholders' Equity\n"
            "Net loss | — | — | — | — | (660) | — | (660)",
        )
        item = {
            "phrase": "net income",
            "queries": ("net income", "net loss"),
            "home": "income",
            "aliases": ("net income", "net earnings", "net loss"),
        }
        chosen = choose_row([summary, equity, operations], item, {"income", "cash_flow"})
        self.assertEqual(chosen["chunk_id"], "ops")


class FormulaTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            _hit(
                "printed",
                43,
                "Summary of cash flow data\nDays payable outstanding | 44 | 48",
            ),
            _hit(
                "ap",
                72,
                "Consolidated Balance Sheets\nColumns: 2020 | 2019\n"
                "Accounts payable | 1,174 | 1,587",
            ),
            _hit(
                "inv",
                72,
                "Consolidated Balance Sheets\nColumns: 2020 | 2019\n"
                "Inventories, net (Note 6) | 2,438 | 2,320",
            ),
            _hit(
                "cogs",
                70,
                "Consolidated Statements of Income\nColumns: 2020 | 2019 | 2018\n"
                "Cost of sales | 7,772 | 7,468 | 6,829",
            ),
        ]

    def test_printed_ratio_is_removed_when_the_question_defines_a_formula(self):
        kept = without_printed_metric(DPO, self.rows)
        self.assertEqual([row["chunk_id"] for row in kept], ["ap", "inv", "cogs"])

    def test_defined_formula_is_computed_from_the_statement_rows(self):
        answer = answer_from_formula(DPO, self.rows)
        self.assertIsNotNone(answer)
        self.assertEqual(answer["status"], "answered")
        self.assertTrue(answer["calculation"]["verified"], answer["calculation"]["errors"])
        self.assertAlmostEqual(answer["calculation"]["final"]["computed"], 63.863436, places=4)
        self.assertIn("63.86", answer["answer"])
        pages = sorted(item["page"] for item in answer["citations"])
        self.assertEqual(pages, [70, 72, 72])

    def test_missing_operand_is_left_for_the_model(self):
        self.assertIsNone(answer_from_formula(DPO, self.rows[:3]))


class ReportedLineTests(unittest.TestCase):
    def test_thousands_are_converted_to_the_asked_millions(self):
        question = (
            "Basing your judgments off of the balance sheet, what is the year end "
            "FY2018 amount of accounts payable for MGM Resorts? Answer in USD millions."
        )
        row = _hit(
            "ap",
            57,
            "CONSOLIDATED BALANCE SHEETS\n"
            "(In thousands, except share data)\n"
            "Columns: December 31, 2018 | December 31, 2017\n"
            "Accounts payable | $ 302,578 | $ 255,028",
        )
        answer = answer_from_reported_line(question, [row])
        self.assertIsNotNone(answer)
        self.assertEqual(answer["status"], "answered")
        self.assertTrue(answer["answer"].startswith("303 million"))
        self.assertNotIn("average", answer["answer"])
        self.assertTrue(answer["calculation"]["verified"], answer["calculation"]["errors"])
        self.assertEqual(answer["calculation"]["final"]["stated"], 303)
        self.assertEqual(answer["citations"][0]["page"], 57)

    def test_a_figure_already_in_millions_is_copied(self):
        question = (
            "We need to calculate a financial metric by using information only provided "
            "within the balance sheet. Please answer the following question: what is "
            "Boeing's year end FY2018 net property, plant, and equipment (in USD millions)?"
        )
        row = _hit(
            "ppe",
            52,
            "CONSOLIDATED STATEMENTS OF FINANCIAL POSITION\n"
            "(In millions, except per share data)\n"
            "Columns: 2018 | 2017\n"
            "Property, plant and equipment, net | 12,645 | 12,672",
        )
        answer = answer_from_reported_line(question, [row])
        self.assertIsNotNone(answer)
        self.assertEqual(answer["status"], "answered")
        self.assertTrue(answer["answer"].startswith("12645 million"))
        self.assertNotIn("calculation", answer)

    def test_a_margin_question_is_not_a_single_line(self):
        question = (
            "According to the P&L statement, what is the FY2015 depreciation and "
            "amortization % margin for AMD?"
        )
        self.assertIsNone(answer_from_reported_line(question, []))


if __name__ == "__main__":
    unittest.main()
