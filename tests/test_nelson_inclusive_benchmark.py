import os
import unittest

os.environ.setdefault("DATABASE_URL", "postgresql://unused")

from analyses import nelson_inclusive_benchmark as benchmark


class NelsonInclusiveBenchmarkTests(unittest.TestCase):
    def test_tiers_are_encoded_in_order(self):
        rows = [
            {"target_id": 1, "indication_id": i, "nelson_tier": tier}
            for i, tier in enumerate(("T0", "T1", "T2", "T3"), 1)
        ]
        self.assertEqual(benchmark.encode_nelson_tiers(rows).tolist(), [0, 1, 2, 3])

    def test_uncovered_pair_is_conservatively_encoded_as_t0(self):
        values = benchmark.encode_nelson_tiers(
            [{"target_id": 1, "indication_id": 2, "nelson_tier": None}]
        )
        self.assertEqual(values.tolist(), [0])

    def test_invalid_tier_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing or invalid"):
            benchmark.encode_nelson_tiers(
                [{"target_id": 1, "indication_id": 2, "nelson_tier": "T4"}]
            )


if __name__ == "__main__":
    unittest.main()
