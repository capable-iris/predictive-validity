import os
import unittest

import numpy as np

os.environ.setdefault("DATABASE_URL", "postgresql://unused")

from benchmark import scorers_ensemble, scorers_ml


class FeatureMissingnessPolicyTests(unittest.TestCase):
    def test_structural_absence_is_zero_but_unknown_measurement_stays_nan(self):
        vector = scorers_ml.row_to_feature_vector({})
        index = {name: i for i, name in enumerate(scorers_ml.FEATURE_NAMES)}

        self.assertEqual(vector[index["gwas_n_sig"]], 0.0)
        self.assertEqual(vector[index["n_hpo_phenotypes"]], 0.0)
        self.assertEqual(vector[index["n_dgidb_drugs"]], 0.0)
        self.assertEqual(vector[index["ot_is_mendelian_any"]], 0.0)
        self.assertTrue(np.isnan(vector[index["gnomad_loeuf"]]))
        self.assertTrue(np.isnan(vector[index["depmap_mean_effect"]]))
        self.assertTrue(np.isnan(vector[index["depmap_pan_essential"]]))

    def test_log_transforms_preserve_unknowns_for_fold_local_imputation(self):
        values = np.array([[np.nan], [3.0]])
        for transform in (
            scorers_ml.log_transform_features,
            scorers_ensemble.log_transform_features,
        ):
            transformed = transform(values, ["gwas_n_sig"])
            self.assertTrue(np.isnan(transformed[0, 0]))
            self.assertAlmostEqual(transformed[1, 0], np.log(4.0))

    def test_unavailable_or_post_outcome_features_are_not_predictors(self):
        for feature in (
            "ot_l2g_score_max",
            "family_approved_count",
            "gene_approved_count",
        ):
            self.assertNotIn(feature, scorers_ml.FEATURE_NAMES)
            self.assertIn(feature, scorers_ml.EXCLUDED_PREDICTIVE_FEATURES)


if __name__ == "__main__":
    unittest.main()
