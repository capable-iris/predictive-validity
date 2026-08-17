"""Held-out-TARGET cross-validation.

Standard k-fold splits T-I pairs randomly. But if multiple T-I pairs share the
same target (e.g., PDCD1 across many oncology indications), they leak
information into each other's evaluation. This is the "target leakage" test.

Group k-fold on target_id: split so that all T-Is with the same target are in
either train or test but not both. This is the stringent measure of whether
the model has learned generalizable biology vs memorized targets.
"""

import os
import sys
from collections import Counter
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


def group_cv_predict(model_ctor, X, y, groups, n_splits=5):
    """K-fold with all T-Is sharing a target constrained to same fold."""
    gkf = GroupKFold(n_splits=n_splits)
    oof = np.zeros(len(y), dtype=np.float64)
    for train_idx, test_idx in gkf.split(X, y, groups=groups):
        model = model_ctor()
        model.fit(X[train_idx], y[train_idx])
        oof[test_idx] = model.predict_proba(X[test_idx])[:, 1]
    return oof


def main():
    print("Loading strict Phase 2+ cohort ...")
    rows = _robust.load_strict()
    X = np.stack([_ml.row_to_feature_vector(r) for r in rows])
    y = np.array([1 if r["y_strict"] else 0 for r in rows], dtype=np.int64)
    groups = np.array([r["target_id"] for r in rows], dtype=np.int64)
    n_unique_targets = len(set(groups))
    print(f"  cohort: n={len(rows)}, unique targets: {n_unique_targets}, pos rate: {y.mean():.4f}")

    X_log = _stack.log_transform_features(X, _ml.FEATURE_NAMES)

    for scorer_name, ctor in [
        ("logreg_holdout_target_v2_hpo", _stack.make_logreg_l2),
        ("lightgbm_robust_holdout_target_v2_hpo", _robust.make_lgb_robust),
    ]:
        print(f"\n== {scorer_name} — held-out TARGETS (5-fold group CV) ==")
        oof = group_cv_predict(ctor, X_log, y, groups, n_splits=5)
        y_list, p_list = y.tolist(), oof.tolist()

        auc, auc_lo, auc_hi = _runner.bootstrap_metric(y_list, p_list, _runner.auc_roc)
        brier = _runner.brier_score(y_list, p_list)
        r10 = _runner.recall_at_top_k(y_list, p_list, 0.10)
        p10 = _runner.precision_at_top_k(y_list, p_list, 0.10)
        rs10 = _runner.rs_by_top_decile(y_list, p_list)
        ece = _runner.calibration_ece(y_list, p_list)
        print(f"  AUC = {auc:.3f} [{auc_lo:.3f}, {auc_hi:.3f}]")
        print(f"  Brier = {brier:.3f}, R@10% = {r10:.3f}, P@10% = {p10:.3f}, "
              f"RS10 = {rs10:.2f}, ECE = {ece:.3f}")

        conn = psycopg2.connect(DB_URL)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO preclin.benchmark_run
                  (scoring_function, scoring_version, cohort_definition,
                   n_ti_pairs, n_approved, n_failed,
                   auc_roc, auc_roc_ci_lo, auc_roc_ci_hi, brier_score,
                   recall_at_10pct, precision_at_10pct, rs_top_decile,
                   calibration_ece, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (scorer_name, "v2_hpo", "ti_phase2plus_strict_holdout_target",
                  len(rows), int(y.sum()), int(len(rows) - y.sum()),
                  auc, auc_lo, auc_hi, brier, r10, p10, rs10, ece,
                  "Group 5-fold CV by target_id — all T-Is sharing a target in same fold. "
                  "Tests generalization to unseen targets; corrected HPO."))
            conn.commit()
        conn.close()


if __name__ == "__main__":
    main()
