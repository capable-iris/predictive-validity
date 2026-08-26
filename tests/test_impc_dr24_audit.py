import csv
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ImpcDr24AuditTests(unittest.TestCase):
    def test_audit_has_only_supported_numeric_values(self):
        with (ROOT / "data" / "impc_missing_update_dr24.csv").open() as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 444)
        eligible_zeros = [r for r in rows if r["eligible_observed_zero"] == "True"]
        unambiguous_positive = [
            r for r in rows
            if r["status"] == "current_significant_phenotype"
            and r["mapping_ambiguous"] == "False"
        ]
        self.assertEqual(len(eligible_zeros), 26)
        self.assertEqual(len(unambiguous_positive), 6)
        self.assertTrue(all(
            int(r["homozygous_tested_procedures"]) >= 13
            and r["mapping_ambiguous"] == "False"
            and int(r["distinct_significant_mp_terms_sum"]) == 0
            for r in eligible_zeros
        ))

    def test_feature_change_ledger_is_complete(self):
        with (ROOT / "data" / "impc_dr24_feature_changes.csv").open() as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 444)
        self.assertEqual(Counter(r["change"] for r in rows), Counter({
            "synthetic_zero_to_unknown": 353,
            "synthetic_zero_to_observed": 6,
            "unknown_to_observed": 6,
            "unchanged": 20,
            "remains_unknown": 59,
        }))


if __name__ == "__main__":
    unittest.main()
