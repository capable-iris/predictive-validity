"""Regression tests for target-disjoint category ablations."""

from __future__ import annotations

import os
import unittest

import numpy as np


os.environ.setdefault("DATABASE_URL", "postgresql://unused")

from analyses import ablation


class AblationGroupingTests(unittest.TestCase):
    def test_each_target_is_confined_to_one_side_of_each_fold(self):
        groups = np.repeat(np.arange(10), 2)
        features = np.zeros((len(groups), 1))
        labels = np.tile([0, 1], 10)
        folds = list(
            ablation.held_out_target_splits(
                features, labels, groups, n_splits=5
            )
        )
        self.assertEqual(len(folds), 5)
        for train_index, test_index in folds:
            self.assertFalse(set(groups[train_index]) & set(groups[test_index]))


if __name__ == "__main__":
    unittest.main()
