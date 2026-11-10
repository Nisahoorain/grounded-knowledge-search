import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.answering.calculation import review_supported_answer, verify_calculation
from src.answering.generator import verify_claims

QUESTION = (
    "Based on the information provided primarily in the balance sheet and the "
    "statement of income, what is FY2020 days payable outstanding (DPO) for Corning? "
    "DPO is defined as: 365 * (average accounts payable between FY2019 and FY2020) / "
    "(FY2020 COGS + change in inventory between FY2019 and FY2020). "
    "Round your answer to two decimal places."
)

CITATIONS = {
    "E1": {"doc_id": "CORNING_2020_10K", "page": 72, "chunk_id": "p72"},
    "E2": {"doc_id": "CORNING_2020_10K", "page": 70, "chunk_id": "p70"},
}
CHUNKS = {
    "E1": "December 31, 2020 and 2019\nAccounts payable 1,174 1,587\nInventories, net 2,438 2,320",
    "E2": "Cost of sales 7,772",
}


def _review(answer):
    return review_supported_answer(answer, QUESTION, CITATIONS, CHUNKS)


class ClaimCheckTests(unittest.TestCase):
    def test_copied_number_stays_supported_without_a_calculation(self):
        answer = "Cost of sales was $7,772 million [E2]."
        reviewed = _review(answer)
        self.assertEqual(reviewed["status"], "answered")
        self.assertNotIn("calculation", reviewed)
        self.assertEqual(reviewed["verification"], [{
            "sentence": answer,
            "cited": ["E2"],
            "uncited": False,
            "missing_numbers": [],
            "supported": True,
        }])

    def test_derived_number_without_a_block_is_still_unverified(self):
        answer = "FY2020 days payable outstanding is 63.86 days [E1]."
        reviewed = _review(answer)
        self.assertEqual(reviewed["status"], "answered_unverified")
        self.assertNotIn("calculation", reviewed)
        self.assertEqual(reviewed["unsupported_claims"][0]["missing_numbers"], ["63.86"])

    def test_verify_claims_positional_call_is_unchanged(self):
        claims = verify_claims(
            "Cost of sales was $7,772 million [E2].",
            CITATIONS,
            CHUNKS,
        )
        self.assertTrue(claims[0]["supported"])
        self.assertEqual(claims[0]["missing_numbers"], [])


class DpoCalculationTests(unittest.TestCase):
    def _correct_block(self):
        return """
FY2020 days payable outstanding is 63.86 days [E1].
```calc
formula: days * average_ap / (cogs + inventory_change)
input: ap_2020 = 1174 [E1]
input: ap_2019 = 1,587 [E1]
input: inventory_2020 = 2,438 [E1]
input: inventory_2019 = 2320 [E1]
input: cogs = 7,772 [E2]
input: days = 365 [Q]
step: average_ap = (ap_2020 + ap_2019) / 2 = 1380.5
step: inventory_change = inventory_2020 - inventory_2019 = 118
step: purchases = cogs + inventory_change = 7890
step: dpo = days * average_ap / purchases = 63.8634
final: 63.86
```
"""

    def test_correct_chain_recomputes_63_86(self):
        reviewed = _review(self._correct_block())
        calc = reviewed["calculation"]
        expected = 365 * ((1174 + 1587) / 2) / (7772 + (2438 - 2320))
        by_name = {step["name"]: step for step in calc["steps"]}

        self.assertTrue(calc["verified"], calc["errors"])
        self.assertEqual(reviewed["status"], "answered")
        self.assertEqual(reviewed["unsupported_claims"], [])
        self.assertNotIn("derived_numbers", calc)
        self.assertEqual(by_name["average_ap"]["computed"], 1380.5)
        self.assertEqual(by_name["inventory_change"]["computed"], 118)
        self.assertEqual(by_name["purchases"]["computed"], 7890)
        self.assertTrue(by_name["average_ap"]["ok"])
        self.assertTrue(by_name["inventory_change"]["ok"])
        self.assertAlmostEqual(calc["final"]["computed"], expected, places=9)
        self.assertTrue(calc["final"]["ok"])
        self.assertAlmostEqual(expected, 63.86, places=2)

    def test_stated_63_86_fails_when_inventory_change_is_left_out(self):
        answer = """
FY2020 days payable outstanding is 63.86 days [E1].
```calc
formula: days * average_ap / cogs
input: ap_2020 = 1174 [E1]
input: ap_2019 = 1587 [E1]
input: cogs = 7772 [E2]
input: days = 365 [Q]
step: average_ap = (ap_2020 + ap_2019) / 2 = 1380.5
step: dpo = days * average_ap / cogs = 63.86
final: 63.86
```
"""
        calc = _review(answer)["calculation"]
        self.assertFalse(calc["verified"])
        self.assertFalse(calc["final"]["ok"])
        self.assertTrue(calc["steps"][0]["ok"])
        self.assertAlmostEqual(calc["final"]["computed"], 365 * 1380.5 / 7772, places=6)
        self.assertTrue(any("63.86" in error for error in calc["errors"]))

    def test_averages_and_changes_cannot_be_cited_as_inputs(self):
        answer = """
FY2020 days payable outstanding is 63.86 days [E1].
```calc
formula: days * average_ap / (cogs + inventory_change)
input: average_ap = 1,380.5 [E1]
input: inventory_change = 118 [E1]
input: cogs = 7,772 [E2]
input: days = 365 [Q]
step: denominator = cogs + inventory_change = 7890
step: dpo = days * average_ap / denominator = 63.86
final: 63.86
```
"""
        reviewed = _review(answer)
        calc = reviewed["calculation"]
        by_name = {item["name"]: item for item in calc["inputs"]}

        self.assertFalse(calc["verified"])
        self.assertEqual(reviewed["status"], "answered_unverified")
        self.assertFalse(by_name["average_ap"]["in_source"])
        self.assertFalse(by_name["inventory_change"]["in_source"])
        self.assertTrue(by_name["cogs"]["in_source"])
        self.assertTrue(by_name["days"]["in_source"])
        self.assertTrue(calc["final"]["ok"])
        self.assertIn("63.86", reviewed["unsupported_claims"][0]["missing_numbers"])

    def test_days_constant_must_come_from_the_question(self):
        block = self._correct_block().replace("input: days = 365 [Q]", "input: days = 365 [E1]")
        calc = _review(block)["calculation"]
        days = next(item for item in calc["inputs"] if item["name"] == "days")
        self.assertFalse(days["in_source"])
        self.assertFalse(calc["verified"])

    def test_result_cannot_be_planted_as_a_literal(self):
        answer = """
The computed figure is 63.86 [E2].
```calc
formula: cogs / cogs * 63.86
input: cogs = 7772 [E2]
step: identity = cogs / cogs = 1
final: 63.86
```
"""
        calc = _review(answer)["calculation"]
        self.assertFalse(calc["verified"])
        self.assertTrue(any("63.86" in error for error in calc["errors"]))

    def test_negative_input_must_be_parenthetical_or_signed_in_the_chunk(self):
        block = """
formula: change + base
input: change = (10) [E1]
input: base = 5 [E1]
step: total = change + base = -5
final: -5
"""
        citations = {"E1": CITATIONS["E1"]}
        ok = verify_calculation(block, "none", citations, {"E1": "adjustment (10) and base 5"})
        self.assertTrue(ok["verified"], ok["errors"])
        self.assertEqual(ok["final"]["computed"], -5)

        bad = verify_calculation(block, "none", citations, {"E1": "adjustment 10 and base 5"})
        change = next(item for item in bad["inputs"] if item["name"] == "change")
        self.assertFalse(change["in_source"])
        self.assertFalse(bad["verified"])

    def test_unclosed_block_does_not_count_as_verified(self):
        answer = "Cost of sales was $7,772 million [E2].\n```calc\nformula: cogs\n"
        reviewed = _review(answer)
        self.assertEqual(reviewed["status"], "answered_unverified")
        self.assertFalse(reviewed["calculation"]["verified"])
        self.assertTrue(any("closing fence" in error for error in reviewed["calculation"]["errors"]))


if __name__ == "__main__":
    unittest.main()
