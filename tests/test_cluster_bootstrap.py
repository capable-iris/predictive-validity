import os
import unittest

os.environ.setdefault("DATABASE_URL", "postgresql://unused")

from benchmark import runner


class ClusterBootstrapTests(unittest.TestCase):
    def test_perfect_auc_remains_perfect_when_complete_targets_are_resampled(self):
        y = [False, False, True, True]
        predictions = [0.1, 0.2, 0.8, 0.9]
        groups = [10, 10, 20, 20]
        point, low, high = runner.cluster_bootstrap_metric(
            y, predictions, groups, runner.auc_roc, n_iter=200, seed=7
        )
        self.assertEqual((point, low, high), (1.0, 1.0, 1.0))

    def test_cluster_bootstrap_is_seeded_and_reproducible(self):
        args = (
            [False, True, False, True, False, True],
            [0.1, 0.4, 0.3, 0.8, 0.6, 0.7],
            [1, 1, 2, 2, 3, 3],
            runner.auc_roc,
        )
        first = runner.cluster_bootstrap_metric(*args, n_iter=250, seed=42)
        second = runner.cluster_bootstrap_metric(*args, n_iter=250, seed=42)
        self.assertEqual(first, second)

    def test_mismatched_group_vector_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "equal length"):
            runner.cluster_bootstrap_metric(
                [False, True], [0.1, 0.9], [1], runner.auc_roc
            )


if __name__ == "__main__":
    unittest.main()
