"""Regression tests for cohort-wide Nelson-tier preparation."""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from analyses.classifiers import nelson_tier_classify as nelson


class FakePairCursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return self.rows


class NelsonTierClassifierTests(unittest.TestCase):
    def test_all_clinical_enumerator_is_outcome_blind_and_groups_drugs(self):
        cur = FakePairCursor(
            [
                (10, "GENE1", 20, "Disease A", 1, "Drug A", "small_molecule"),
                (10, "GENE1", 20, "Disease A", 2, "Drug B", "mAb"),
                (11, "GENE2", 21, "Disease B", 3, "Drug C", "protein"),
            ]
        )
        pairs = nelson.all_clinical_pairs(cur)
        self.assertEqual(len(pairs), 2)
        self.assertEqual(pairs[0].key, "10:20")
        self.assertEqual(len(pairs[0].drugs), 2)
        sql = cur.sql.lower()
        self.assertNotIn("approval", sql)
        self.assertNotIn("outcome", sql)
        self.assertNotIn("highest_phase", sql)

    def test_dossier_and_prompt_preserve_all_documents_when_under_budget(self):
        pair = nelson.Pair(10, "GENE1", 20, "Disease A")
        structured = {
            "mendelian_associations": [],
            "clingen_validity": [],
            "gwas_associations": [],
            "open_targets_evidence": [],
        }
        documents = [
            {
                "source_document_id": i,
                "pmid": str(i),
                "title": f"Title {i}",
                "abstract": "full text",
            }
            for i in range(25)
        ]
        dossier = nelson.build_dossier(pair, structured, documents)
        prompt = nelson.prompt_evidence(dossier)
        self.assertEqual(len(dossier["evidence"]["pubmed_documents"]), 25)
        self.assertEqual(dossier["evidence_counts"]["pubmed_documents"], 25)
        self.assertEqual(len(prompt["pubmed_documents"]), 25)
        self.assertFalse(prompt["selection_summary"]["overflow"])

    def test_only_overflow_uses_indication_terms_for_ordering(self):
        pair = nelson.Pair(10, "GENE1", 20, "Psoriasis")
        structured = {
            "mendelian_associations": [],
            "clingen_validity": [],
            "gwas_associations": [
                {"trait": f"height record {index}", "context": "x" * 100}
                for index in range(20)
            ] + [{"trait": "psoriasis susceptibility", "context": "x" * 100}],
            "open_targets_evidence": [],
        }
        dossier = nelson.build_dossier(pair, structured, [])
        prompt = nelson.prompt_evidence(dossier, max_chars=600)
        self.assertTrue(prompt["selection_summary"]["overflow"])
        self.assertEqual(prompt["gwas_associations"][0]["trait"], "psoriasis susceptibility")
        self.assertGreater(prompt["selection_summary"]["dropped"]["gwas_associations"], 0)

    def test_cited_pmids_collects_gwas_and_open_targets_references(self):
        evidence = {
            "gwas_associations": [{"study_pmid": "PMID: 123"}],
            "open_targets_evidence": [{"key_pmids": ["456", "123"]}],
        }
        self.assertEqual(nelson.cited_pmids(evidence), ["123", "456"])

    def test_model_cannot_cite_literature_absent_from_dossier(self):
        pair = nelson.Pair(10, "GENE1", 20, "Disease A")
        dossier = nelson.build_dossier(
            pair,
            {
                "mendelian_associations": [],
                "clingen_validity": [],
                "gwas_associations": [],
                "open_targets_evidence": [],
            },
            [],
        )
        response = SimpleNamespace(
            text=json.dumps(
                {
                    "tier": "T1",
                    "direction_concordance": "unclear",
                    "disease_match": "unclear",
                    "supporting_pmids": ["999"],
                }
            ),
            model="test-model",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0,
        )
        with patch.object(nelson, "call_with_retry", return_value=response):
            with self.assertRaisesRegex(ValueError, "absent from dossier"):
                nelson.score_one_pair(object(), dossier, "test-model")

    def test_canonical_prompt_documents_are_linked_to_the_model_run(self):
        pair = nelson.Pair(10, "GENE1", 20, "Disease A")
        dossier = nelson.build_dossier(
            pair,
            {
                "mendelian_associations": [],
                "clingen_validity": [],
                "gwas_associations": [],
                "open_targets_evidence": [],
            },
            [{
                "source_document_id": 99,
                "pmid": "123",
                "title": "Title",
                "abstract": "Exact abstract text",
            }],
        )
        response = SimpleNamespace(
            text=json.dumps({
                "tier": "T1",
                "direction_concordance": "unclear",
                "disease_match": "related",
                "supporting_pmids": ["123"],
            }),
            model="test-model",
            provider_request_id="request-1",
            system_prompt=nelson.SYSTEM_PROMPT,
            user_prompt="rendered prompt",
            max_tokens=1024,
            temperature=0.0,
            input_tokens=10,
            output_tokens=5,
            cost_usd=0,
        )
        with patch.object(nelson, "call_with_retry", return_value=response):
            row = nelson.score_one_pair(object(), dossier, "test-model")
        self.assertEqual(row["schema_version"], "nelson_tier_result_v3")
        self.assertEqual(row["_source_documents"], [{
            "source_document_id": 99,
            "relationship": "pubmed_abstract_input",
            "ordinal": 0,
            "excerpt_text": "Exact abstract text",
        }])


if __name__ == "__main__":
    unittest.main()
