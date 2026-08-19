"""Leave-one-category-out ablation on the strict outcome.

Uses held-out-target 5-fold CV so target-indication pairs sharing a target never
appear in both training and test data.
"""

import os
import sys
import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor
from sklearn.model_selection import GroupKFold

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'benchmark'))
from importlib import import_module
_runner = import_module("runner")
_ml = import_module("scorers_ml")
_robust = import_module("scorers_ml")
_stack = import_module("scorers_ensemble")

DB_URL = os.environ["DATABASE_URL"]

CATEGORIES = ["A_genetics", "B_mechanistic", "C_cell", "D_animal",
              "E_pd", "H_safety", "I_landscape", "context"]


def category_for_feature(name, category_map=None):
    """Return the reporting category for a model feature."""
    category_map = category_map or _ml.FEATURE_CATEGORIES
    if name.startswith("nelson_"):
        return "A_genetics"
    if name.startswith("ta_"):
        return "context"
    return category_map.get(name)


def mask_category(X, category, category_map=None):
    """Mask one evidence category without changing feature order."""
    masked = X.copy()
    for index, name in enumerate(_ml.FEATURE_NAMES):
        if category_for_feature(name, category_map) == category:
            masked[:, index] = np.nan
    return masked


def held_out_target_predictions(X, y, groups, n_splits=5):
    """Return OOF predictions with every target confined to one fold."""
    splitter = GroupKFold(n_splits=n_splits)
    predictions = np.zeros(len(y), dtype=np.float64)
    for train_index, test_index in splitter.split(X, y, groups=groups):
        model = _stack.make_logreg_l2()
        model.fit(X[train_index], y[train_index])
        predictions[test_index] = model.predict_proba(X[test_index])[:, 1]
    return predictions


def evaluate_ablation(X, y, groups, category_map=None):
    """Return full and leave-one-category-out AUCs on identical folds."""
    full_predictions = held_out_target_predictions(X, y, groups)
    full_auc = _runner.auc_roc(y.tolist(), full_predictions.tolist())
    results = []
    for category in CATEGORIES:
        masked = mask_category(X, category, category_map)
        predictions = held_out_target_predictions(masked, y, groups)
        auc = _runner.auc_roc(y.tolist(), predictions.tolist())
        results.append((category, auc, auc - full_auc))
    return full_auc, results


def main():
    rows = _robust.load_strict()
    X = np.stack([_ml.row_to_feature_vector(r) for r in rows])
    y = np.array([1 if r["y_strict"] else 0 for r in rows], dtype=np.int64)
    groups = np.array([r["target_id"] for r in rows], dtype=np.int64)
    X_log = _stack.log_transform_features(X, _ml.FEATURE_NAMES)
    print(f"Cohort: {len(rows)}, targets: {len(set(groups))}, positive: {y.mean():.4f}")

    auc_full, results = evaluate_ablation(X_log, y, groups)
    print(f"\nFull LogReg model AUC (strict): {auc_full:.3f}")

    print("\nLeave-one-category-out (LogReg, GroupKFold(target), strict):")
    print(f"{'Category':<16} {'AUC':<8} {'ΔAUC':<10}")
    print("-" * 40)

    for cat, auc_ab, delta in results:
        print(f"{cat:<16} {auc_ab:.3f}   {delta:+.4f}")
        # Store
        conn = psycopg2.connect(DB_URL)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO preclin.benchmark_run
                  (scoring_function, scoring_version, cohort_definition,
                   n_ti_pairs, n_approved, n_failed, auc_roc, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (f"logreg_strict_ablate_no_{cat}_holdout_target", "holdout_target_v1",
                  "ti_phase2plus_strict_holdout_target", len(rows), int(y.sum()),
                  int(len(y) - y.sum()), auc_ab,
                  f"Ablate {cat}, strict outcome, LogReg, GroupKFold(target_id). "
                  f"Full AUC={auc_full:.3f}, delta={delta:+.4f}"))
            conn.commit()
        conn.close()

    print("\nSorted by AUC drop (most load-bearing first):")
    for cat, auc, delta in sorted(results, key=lambda x: x[2]):
        print(f"  {cat:<16} ΔAUC={delta:+.4f}")


if __name__ == "__main__":
    main()
