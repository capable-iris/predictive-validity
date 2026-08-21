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


class RecordingEvidenceCursor:
    def __init__(self):
        self.queries = []
        self.description = [("placeholder",)]

    def execute(self, sql, params=None):
        self.queries.append(sql)

    def fetchall(self):
        return []


class NelsonTierClassifierTests(unittest.TestCase):
    def test_all_clinical_enumerator_is_outcome_blind_and_groups_drugs(self):
        cur = FakePairCursor(
            [
                (10, "GENE1", 20, "Disease A", "Disease A", None, None,
                 1, "Drug A", "small_molecule", "inhibitor"),
                (10, "GENE1", 20, "Disease A", "Disease A", None, None,
                 2, "Drug B", "mAb", "antagonist"),
                (11, "GENE2", 21, "Disease B", "Disease B", None, None,
                 3, "Drug C", "protein", "agonist"),
            ]
        )
        pairs = nelson.all_clinical_pairs(cur)
        self.assertEqual(len(pairs), 2)
        self.assertEqual(pairs[0].key, "10:20")
        self.assertEqual(len(pairs[0].drugs), 2)
        self.assertEqual(pairs[0].drugs[0]["mechanism"], "inhibitor")
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
            "open_targets_genetic_evidence": [],
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
            "open_targets_genetic_evidence": [],
        }
        dossier = nelson.build_dossier(pair, structured, [])
        prompt = nelson.prompt_evidence(dossier, max_chars=2500)
        self.assertTrue(prompt["selection_summary"]["overflow"])
        self.assertEqual(prompt["gwas_associations"][0]["trait"], "psoriasis susceptibility")
        self.assertGreater(prompt["selection_summary"]["dropped"]["gwas_associations"], 0)

    def test_cited_pmids_collects_gwas_and_open_targets_references(self):
        evidence = {
            "gwas_associations": [{"study_pmid": "PMID: 123"}],
            "open_targets_genetic_evidence": [{"key_pmids": ["456", "123"]}],
        }
        self.assertEqual(nelson.cited_pmids(evidence), ["123", "456"])

    def test_open_targets_query_is_genetics_only(self):
        cur = RecordingEvidenceCursor()
        evidence = nelson.fetch_target_evidence(cur, 10)
        sql = cur.queries[-1].lower()
        self.assertIn("evidence_type", sql)
        self.assertIn("!~* 'somatic'", sql)
        self.assertNotIn("literature_score", sql)
        self.assertNotIn("overall_score", sql)
        self.assertIn("open_targets_genetic_evidence", evidence)

    def test_model_cannot_cite_literature_absent_from_dossier(self):
        pair = nelson.Pair(10, "GENE1", 20, "Disease A")
        dossier = nelson.build_dossier(
            pair,
            {
                "mendelian_associations": [],
                "clingen_validity": [],
                "gwas_associations": [{
                    "id": 1,
                    "trait": "Disease A",
                    "p_value": 1e-10,
                    "context": "intron_variant",
                    "study_accession": "GCST1",
                    "study_pmid": "123",
                }],
                "open_targets_genetic_evidence": [],
            },
            [],
        )
        dossier["dossier_source_document_id"] = 98
        response = SimpleNamespace(
            text=json.dumps(
                {
                    "tier": "T1",
                    "genetic_effect_direction": "unclear",
                    "disease_match": "exact",
                    "supporting_evidence": [{
                        "evidence_id": "gwas:1",
                        "disease_match": "exact",
                        "rationale": "The GWAS trait matches Disease A.",
                    }],
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
                "open_targets_genetic_evidence": [],
            },
            [{
                "source_document_id": 99,
                "pmid": "123",
                "title": "Title",
                "abstract": "Exact abstract text",
            }],
        )
        dossier["dossier_source_document_id"] = 100
        response = SimpleNamespace(
            text=json.dumps({
                "tier": "T0",
                "genetic_effect_direction": "unclear",
                "disease_match": "unmatched",
                "supporting_evidence": [],
                "supporting_pmids": [],
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
        self.assertEqual(row["schema_version"], "nelson_tier_result_v5")
        self.assertEqual(row["_source_documents"], [
            {
                "source_document_id": 100,
                "relationship": "dossier_snapshot",
                "ordinal": 0,
                "excerpt_text": None,
            },
            {
                "source_document_id": 99,
                "relationship": "pubmed_abstract_input",
                "ordinal": 0,
                "excerpt_text": "Exact abstract text",
            },
        ])

    def test_dossier_hash_excludes_preparation_timestamp(self):
        pair = nelson.Pair(10, "GENE1", 20, "Disease A")
        structured = {
            "mendelian_associations": [],
            "clingen_validity": [],
            "gwas_associations": [],
            "open_targets_genetic_evidence": [],
        }
        dossier = nelson.build_dossier(pair, structured, [])
        first_hash = nelson.dossier_sha256(dossier)
        dossier["prepared_at"] = "2099-01-01T00:00:00+00:00"
        dossier["dossier_source_document_id"] = 999
        self.assertEqual(nelson.dossier_sha256(dossier), first_hash)

    def test_deterministic_t3_excludes_generic_mendelian_gene_links(self):
        generic = nelson.deterministic_eligibility(
            "mendelian_associations", {"association_type": "gene"}
        )
        causal = nelson.deterministic_eligibility(
            "mendelian_associations",
            {"association_type": "Disease-causing germline mutation(s) in"},
        )
        definitive = nelson.deterministic_eligibility(
            "clingen_validity", {"classification": "Definitive"}
        )
        self.assertEqual(generic["tier_ceiling_if_disease_matched"], "T0")
        self.assertEqual(causal["tier_ceiling_if_disease_matched"], "T3")
        self.assertEqual(definitive["tier_ceiling_if_disease_matched"], "T3")

    def test_t2_requires_distinct_replicated_coding_gwas_studies(self):
        pair = nelson.Pair(10, "GENE1", 20, "Disease A")
        structured = {
            "mendelian_associations": [],
            "clingen_validity": [],
            "gwas_associations": [
                {"id": 1, "trait": "Disease A", "p_value": 1e-10,
                 "context": "missense_variant", "study_accession": "GCST1"},
                {"id": 2, "trait": "Disease A", "p_value": 2e-10,
                 "context": "missense_variant", "study_accession": "GCST2"},
            ],
            "open_targets_genetic_evidence": [],
        }
        selected = nelson.prompt_evidence(nelson.build_dossier(pair, structured, []))
        support = [
            {"evidence_id": "gwas:1", "disease_match": "exact", "rationale": "match"},
            {"evidence_id": "gwas:2", "disease_match": "exact", "rationale": "match"},
        ]
        result = nelson.validate_model_support("T2", support, selected)
        self.assertEqual(result["max_supported_tier"], "T2")
        with self.assertRaisesRegex(ValueError, "exceeds deterministic"):
            nelson.validate_model_support("T2", support[:1], selected)

    def test_ontology_hint_is_nonbinding(self):
        pair = nelson.Pair(
            10, "GENE1", 20, "Psoriasis patients", canonical_disease="Psoriasis"
        )
        hint = nelson.disease_match_hint(pair, {"trait": "psoriasis"})
        self.assertEqual(hint["relation_hint"], "exact_text")
        self.assertFalse(hint["binding"])

    def test_gwas_date_is_preserved_in_deterministic_eligibility(self):
        eligibility = nelson.deterministic_eligibility(
            "gwas_associations",
            {
                "p_value": 1e-10,
                "context": "missense_variant",
                "study_accession": "GCST1",
                "evidence_available_date": "2018-03-02",
                "date_basis": "gwas_catalog_publication_date",
                "date_source_version": "r2026-08-03",
            },
        )
        self.assertEqual(eligibility["evidence_available_date"], "2018-03-02")
        self.assertEqual(eligibility["date_source_version"], "r2026-08-03")


if __name__ == "__main__":
    unittest.main()
