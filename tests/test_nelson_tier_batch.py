"""Tests for asynchronous Nelson batch preparation."""
from __future__ import annotations

import re
import unittest

from analyses.classifiers import nelson_tier_batch as batch
from analyses.classifiers import nelson_tier_classify as nelson


class NelsonTierBatchTests(unittest.TestCase):
    def dossier(self):
        dossier = nelson.build_dossier(
            nelson.Pair(10, "GENE1", 20, "Disease A"),
            {
                "mendelian_associations": [],
                "clingen_validity": [],
                "gwas_associations": [],
                "open_targets_genetic_evidence": [],
            },
            [],
        )
        dossier["dossier_source_document_id"] = 99
        return dossier

    def test_batch_request_has_stable_valid_custom_id_and_auditable_hash(self):
        dossier = self.dossier()
        request, metadata = batch.batch_request(
            dossier, nelson.DEFAULT_MODEL, nelson.DEFAULT_MAX_EVIDENCE_CHARS
        )
        self.assertRegex(request["custom_id"], r"^[A-Za-z0-9_-]{1,64}$")
        self.assertEqual(request["custom_id"], batch.custom_id(dossier["pair_key"]))
        self.assertEqual(metadata["dossier_sha256"], dossier["dossier_sha256"])
        user = request["params"]["messages"][0]["content"]
        self.assertEqual(metadata["input_sha256"], batch.input_sha256(user))
        self.assertEqual(request["params"]["max_tokens"], nelson.MODEL_MAX_TOKENS)

    def test_batch_price_is_half_standard_price(self):
        cost = batch.batch_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
        self.assertEqual(cost, 9.0)

    def test_custom_ids_do_not_expose_pair_key(self):
        value = batch.custom_id("10:20")
        self.assertNotIn("10:20", value)
        self.assertTrue(re.fullmatch(r"nelson_[0-9a-f]{32}", value))


if __name__ == "__main__":
    unittest.main()
