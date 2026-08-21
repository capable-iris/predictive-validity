"""Regression tests for cohort-wide Nelson-tier preparation and ingestion."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from analyses.classifiers import nelson_tier_classify as nelson
from db import nelson_tier_io


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

    def test_dossier_preserves_all_abstracts_while_prompt_is_bounded(self):
        pair = nelson.Pair(10, "GENE1", 20, "Disease A")
        structured = {
            "mendelian_associations": [],
            "clingen_validity": [],
            "gwas_associations": [],
            "open_targets_evidence": [],
        }
        abstracts = [
            {"pmid": str(i), "title": f"Title {i}", "abstract": "full text"}
            for i in range(25)
        ]
        dossier = nelson.build_dossier(pair, structured, abstracts)
        self.assertEqual(len(dossier["evidence"]["pubmed_abstracts"]), 25)
        self.assertEqual(dossier["evidence_counts"]["pubmed_abstracts"], 25)
        self.assertEqual(len(nelson.prompt_evidence(dossier)["pubmed_abstracts"]), 20)

    def test_prompt_prioritizes_indication_matched_records(self):
        pair = nelson.Pair(10, "GENE1", 20, "Psoriasis")
        structured = {
            "mendelian_associations": [],
            "clingen_validity": [],
            "gwas_associations": [
                {"trait": "height"},
                {"trait": "psoriasis susceptibility"},
            ],
            "open_targets_evidence": [],
        }
        dossier = nelson.build_dossier(pair, structured, [])
        prompt = nelson.prompt_evidence(dossier)
        self.assertEqual(prompt["gwas_associations"][0]["trait"], "psoriasis susceptibility")

    def test_cited_pmids_collects_gwas_and_open_targets_references(self):
        evidence = {
            "gwas_associations": [{"study_pmid": "PMID: 123"}],
            "open_targets_evidence": [{"key_pmids": ["456", "123"]}],
        }
        self.assertEqual(nelson.cited_pmids(evidence), ["123", "456"])

    def test_pubmed_xml_parser_preserves_abstract_and_ids(self):
        payload = b"""<PubmedArticleSet><PubmedArticle>
          <MedlineCitation><PMID>123</PMID><Article>
            <ArticleTitle>A genetics study</ArticleTitle>
            <Abstract><AbstractText Label="BACKGROUND">Full abstract text.</AbstractText></Abstract>
            <Journal><Title>Example Journal</Title><JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue></Journal>
            <PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList>
          </Article></MedlineCitation>
          <PubmedData><ArticleIdList><ArticleId IdType="doi">10.1/example</ArticleId></ArticleIdList></PubmedData>
        </PubmedArticle></PubmedArticleSet>"""
        records = nelson.parse_pubmed_xml(payload)
        self.assertEqual(records[0]["pmid"], "123")
        self.assertEqual(records[0]["abstract"], "BACKGROUND: Full abstract text.")
        self.assertEqual(records[0]["article_ids"]["doi"], "10.1/example")

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

    def test_result_discovery_excludes_dossier_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = root / "nelson_tiers_all_v2.jsonl"
            dossier = root / "nelson_tiers_all_v2.dossiers.jsonl"
            result.write_text("\n")
            dossier.write_text("\n")
            self.assertEqual(nelson_tier_io.result_files(root), [result])

    def test_ingest_parser_validates_and_normalizes_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nelson_tiers_test.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "nelson_tier_result_v2",
                        "target_id": 10,
                        "indication_id": 20,
                        "tier": "t3",
                        "supporting_pmids": [123, "123", "456"],
                    }
                )
                + "\n"
            )
            rows = list(nelson_tier_io.iter_tier_results([path]))
        self.assertEqual(rows[0]["tier"], "T3")
        self.assertEqual(rows[0]["supporting_pmids"], ["123", "456"])


if __name__ == "__main__":
    unittest.main()
