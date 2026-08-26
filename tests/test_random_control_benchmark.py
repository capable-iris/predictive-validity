import os
import unittest

import numpy as np

os.environ.setdefault("DATABASE_URL", "postgresql://unused")

from analyses import random_control_benchmark as control


class RandomControlBenchmarkTests(unittest.TestCase):
    def test_random_target_control_is_reproducible(self):
        y = np.asarray([0, 1, 0, 1, 0, 1], dtype=np.int64)
        groups = np.asarray([10, 10, 20, 20, 30, 30], dtype=np.int64)
        first = control.random_target_rank_distribution(y, groups, 200, 42)
        second = control.random_target_rank_distribution(y, groups, 200, 42)
        for metric in ("auc", "recall_at_10pct", "precision_at_10pct", "rs_top_decile"):
            self.assertEqual(first[metric], second[metric])
        np.testing.assert_array_equal(first["auc_samples"], second["auc_samples"])

    def test_random_target_control_rejects_too_small_top_group_only_via_metrics(self):
        y = np.asarray([0, 1, 0, 1], dtype=np.int64)
        groups = np.asarray([1, 1, 2, 2], dtype=np.int64)
        result = control.random_target_rank_distribution(y, groups, 100, 7)
        self.assertEqual(len(result["auc_samples"]), 100)
        self.assertTrue(0.0 <= result["auc"][1] <= 1.0)


if __name__ == "__main__":
    unittest.main()
