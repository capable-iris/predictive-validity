"""Leave-one-category-out ablation on the strict outcome.

This preserves the historical ablation method: shuffled, stratified 5-fold CV
with seed 42. Held-out-target validation is intentionally a separate change.
"""

import os
import sys
import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor

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


def stratified_predictions(X, y, n_splits=5):
    """Historical ablation CV: shuffled StratifiedKFold with seed 42."""
    predictions, _ = _ml.cv_predict(
        _stack.make_logreg_l2, X, y, n_splits=n_splits, seed=42
    )
    return predictions


def evaluate_ablation(X, y, category_map=None):
    """Return full and leave-one-category-out AUCs on identical folds."""
    full_predictions = stratified_predictions(X, y)
    full_auc = _runner.auc_roc(y.tolist(), full_predictions.tolist())
    results = []
    for category in CATEGORIES:
        masked = mask_category(X, category, category_map)
        predictions = stratified_predictions(masked, y)
        auc = _runner.auc_roc(y.tolist(), predictions.tolist())
        results.append((category, auc, auc - full_auc))
    return full_auc, results


def main():
    rows = _robust.load_strict()
    X = np.stack([_ml.row_to_feature_vector(r) for r in rows])
    y = np.array([1 if r["y_strict"] else 0 for r in rows], dtype=np.int64)
    X_log = _stack.log_transform_features(X, _ml.FEATURE_NAMES)
    print(f"Cohort: {len(rows)}, positive: {y.mean():.4f}")

    auc_full, results = evaluate_ablation(X_log, y)
    print(f"\nFull LogReg model AUC (strict): {auc_full:.3f}")

    print("\nLeave-one-category-out (LogReg, stratified 5-fold, strict):")
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
            """, (f"logreg_strict_ablate_no_{cat}_hpo_category", "hpo_category_v1",
                  "ti_phase2plus_strict", len(rows), int(y.sum()),
                  int(len(y) - y.sum()), auc_ab,
                  f"Ablate {cat}, strict outcome, LogReg, shuffled StratifiedKFold(seed=42). "
                  f"Full AUC={auc_full:.3f}, delta={delta:+.4f}"))
            conn.commit()
        conn.close()

    print("\nSorted by AUC drop (most load-bearing first):")
    for cat, auc, delta in sorted(results, key=lambda x: x[2]):
        print(f"  {cat:<16} ΔAUC={delta:+.4f}")


if __name__ == "__main__":
    main()
