"""Unit tests for ontology-grounded HPO phenotype breadth."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hpo_ontology", ROOT / "db/hpo_ontology.py")
hpo = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hpo
spec.loader.exec_module(hpo)


OBO = b"""format-version: 1.2

[Term]
id: HP:0000118
name: Phenotypic abnormality

[Term]
id: HP:1000001
name: Included child
is_a: HP:0000118 ! Phenotypic abnormality

[Term]
id: HP:1000002
name: Included grandchild
is_a: HP:1000001 ! Included child

[Term]
id: HP:0000005
name: Mode of inheritance

[Term]
id: HP:1000003
name: Excluded inheritance child
is_a: HP:0000005 ! Mode of inheritance
"""


class HpoOntologyTests(unittest.TestCase):
    def test_only_phenotypic_abnormality_descendants_are_returned(self):
        with patch.object(hpo, "EXPECTED_TERM_COUNT", 2):
            terms = hpo.phenotypic_abnormality_terms(OBO)
        self.assertEqual(terms, ["HP:1000001", "HP:1000002"])


if __name__ == "__main__":
    unittest.main()
