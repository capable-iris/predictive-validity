"""Regression tests for the temporary Nelson-tier model exclusion."""

from __future__ import annotations

import os
import unittest

import numpy as np


os.environ.setdefault("DATABASE_URL", "postgresql://unused")

from benchmark import runner, scorers_llm_agent, scorers_ml, scorers_rule_based


class NelsonExclusionTests(unittest.TestCase):
    def test_canonical_feature_vector_is_invariant_to_nelson_tier(self):
        without_tier = {
            "therapeutic_area": "other",
            "nelson_tier": None,
        }
        with_tier = {
            "therapeutic_area": "other",
            "nelson_tier": "T4",
        }
        np.testing.assert_array_equal(
            scorers_ml.row_to_feature_vector(without_tier),
            scorers_ml.row_to_feature_vector(with_tier),
        )
        self.assertIn("nelson_tier", scorers_ml.EXCLUDED_PREDICTIVE_FEATURES)
        self.assertFalse(
            any(name.startswith("nelson_") for name in scorers_ml.FEATURE_NAMES)
        )

    def test_rule_based_scorers_do_not_register_or_consume_nelson(self):
        self.assertNotIn("nelson_only_v1", scorers_rule_based.list_scorers())
        base = {"A_genetics": {"mendelian_n": 2}}
        annotated = {
            "A_genetics": {"mendelian_n": 2, "nelson_tier": "T4"}
        }
        self.assertEqual(
            scorers_rule_based.scorer_genetic_only(base, {}),
            scorers_rule_based.scorer_genetic_only(annotated, {}),
        )

    def test_runner_drops_nelson_from_evidence_context(self):
        evidence, _, _ = runner.row_to_evidence_context(
            {"nelson_tier": "T4", "any_approved": False}
        )
        self.assertNotIn("nelson_tier", evidence["A_genetics"])

    def test_llm_dossier_drops_nelson(self):
        dossier = scorers_llm_agent.compact_evidence(
            {
                "target_symbol": "TEST",
                "indication_name": "Test disease",
                "nelson_tier": "T4",
                "mendelian_n": 2,
            }
        )
        self.assertNotIn("Nelson", dossier)
        self.assertNotIn("T4", dossier)


if __name__ == "__main__":
    unittest.main()
