"""Focused tests for the repository-scoped target-report skill."""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".agents" / "skills" / "compile-target-report"
SCRIPTS = SKILL / "scripts"
SAMPLE = ROOT / "tests" / "fixtures" / "target_reports" / "ntrk2_mdd.json"
AGENTS = ROOT / ".codex" / "agents"
EXPECTED_AGENTS = {
    "target_assay_researcher",
    "target_evidence_critic",
    "target_mechanism_researcher",
    "target_modality_researcher",
    "target_phenotype_researcher",
    "target_pipeline_researcher",
    "target_translation_critic",
}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_report = load_module("validate_report", SCRIPTS / "validate_report.py")


class TargetReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with SAMPLE.open(encoding="utf-8") as handle:
            cls.sample = json.load(handle)

    def test_sample_satisfies_report_contract(self) -> None:
        report = validate_report.validate_data(copy.deepcopy(self.sample))
        self.assertEqual(report["target"]["symbol"], "NTRK2")
        self.assertIn(
            "Human PD", {row["category"] for row in report["phenotypes"]}
        )

    def test_contract_rejects_unknown_sources_and_missing_direction(self) -> None:
        unknown_source = copy.deepcopy(self.sample)
        unknown_source["phenotypes"][0]["sources"] = ["S99"]
        with self.assertRaises(validate_report.ReportValidationError):
            validate_report.validate_data(unknown_source)

        missing_direction = copy.deepcopy(self.sample)
        missing_direction["phenotypes"][0].pop("effect_direction")
        with self.assertRaises(validate_report.ReportValidationError):
            validate_report.validate_data(missing_direction)

    def test_renderer_emits_two_analysis_pages_and_source_appendix(self) -> None:
        sys.path.insert(0, str(SCRIPTS))
        try:
            render_report = load_module("render_report", SCRIPTS / "render_report.py")
        finally:
            sys.path.pop(0)

        report = validate_report.validate_data(copy.deepcopy(self.sample))
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "report.pdf"
            render_report.render(report, output)
            validate_report.validate_pdf(output)

    def test_all_required_read_only_agents_are_configured(self) -> None:
        configured = {}
        for path in sorted(AGENTS.glob("*.toml")):
            source = path.read_text(encoding="utf-8")
            name_match = re.search(r'^name = "([^"]+)"$', source, re.MULTILINE)
            self.assertIsNotNone(name_match, f"{path} has no agent name")
            configured[name_match.group(1)] = source

        self.assertEqual(set(configured), EXPECTED_AGENTS)
        for source in configured.values():
            self.assertIn('sandbox_mode = "read-only"', source)
            self.assertIn("Do not edit report files", source)


if __name__ == "__main__":
    unittest.main()
