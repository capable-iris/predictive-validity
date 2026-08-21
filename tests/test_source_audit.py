"""Focused unit tests for source-aware LLM audit invariants."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ingest = load_module("source_audit_ingest", "db/13_ingest_llm_outputs.py")
scorer = load_module(
    "source_audit_target_scorer",
    "analyses/classifiers/score_target_literature.py",
)


class NeverCalledClient:
    class Messages:
        @staticmethod
        def create(**_kwargs):
            raise AssertionError("paid model must not be called")

    messages = Messages()


class FakeClient:
    class Messages:
        @staticmethod
        def create(**_kwargs):
            class Usage:
                input_tokens = 10
                output_tokens = 5

            class Block:
                text = (
                    '{"line_b": 1, "line_c": 2, "line_d": 0, '
                    '"line_e": 0, "notable_pmids": ["123"]}'
                )

            class Response:
                usage = Usage()
                content = [Block()]
                id = "request-test"

            return Response()

    messages = Messages()


class SourceAuditTests(unittest.TestCase):
    def test_noncanonical_abstract_fails_before_model_call(self):
        with self.assertRaisesRegex(ValueError, "non-canonical abstracts"):
            scorer.score_one_target(
                NeverCalledClient(),
                "GENE1",
                [{"pmid": "123", "title": "Title", "abstract": "Text"}],
                "test-model",
            )

    def test_evidence_snapshot_is_complete_and_defensive(self):
        citations = [123, "456"]
        snapshot = ingest.evidence_snapshot(
            subject_type="target",
            subject_id=42,
            dimension="line_c_lit",
            category="C_cell",
            source="pubmed_haiku",
            version="v1",
            model="test-model",
            value_numeric=2.0,
            confidence="high",
            citation_pmids=citations,
        )
        citations.append("789")
        self.assertEqual(snapshot["citation_pmids"], ["123", "456"])
        self.assertEqual(snapshot["value_numeric"], 2.0)
        self.assertEqual(snapshot["subject_id2"], None)
        self.assertEqual(snapshot["extracted_by"], "test-model")

    def test_nelson_snapshot_preserves_pair_key_and_details(self):
        snapshot = ingest.evidence_snapshot(
            subject_type="target_indication",
            subject_id=42,
            subject_id2=7,
            dimension="nelson_tier",
            category="A_genetics",
            source="nelson_llm",
            version="v4",
            model="test-model",
            value_text="T3",
            value_json={"dossier_sha256": "abc"},
        )
        self.assertIn("nelson-tier", ingest.TASKS)
        self.assertEqual(snapshot["subject_id2"], 7)
        self.assertEqual(snapshot["value_text"], "T3")
        self.assertEqual(snapshot["value_json"], {"dossier_sha256": "abc"})

    def test_canonical_abstract_produces_complete_source_links(self):
        row = scorer.score_one_target(
            FakeClient(),
            "GENE1",
            [
                {
                    "source_document_id": 99,
                    "pmid": "123",
                    "title": "Title",
                    "abstract": "Text",
                }
            ],
            "test-model",
        )
        self.assertEqual(row["_n_abstracts_provided"], 1)
        self.assertEqual(len(row["_source_documents"]), 1)
        self.assertEqual(row["_source_documents"][0]["source_document_id"], 99)
        ingest.require_source_inputs(row, "target-literature", expected_count=1)


if __name__ == "__main__":
    unittest.main()
