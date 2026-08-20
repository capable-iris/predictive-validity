"""Final benchmark — Phase 1+ strict cohort with held-out-target CV.

This is our most stringent evaluation:
- Strict outcome (approved for THIS indication)
- Phase 1+ cohort (no phase filter)
- 5-fold GroupKFold on target_id (no target appears in both train and test)
- Stacked ensemble with log-transformed features

If this holds up, it's the honest predictive-validity claim.
"""

import os
import sys
import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor
from sklearn.model_selection import GroupKFold
from sklearn.linear_model import LogisticRegression

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'benchmark'))
from importlib import import_module
_ml = import_module("scorers_ml")
_robust = import_module("scorers_ml")
_stack = import_module("scorers_ensemble")
_ph1 = import_module("phase1_cohort")

DB_URL = os.environ["DATABASE_URL"]


def group_stacked_cv(X, y, groups, base_ctors, n_splits=5):
    """Stacked ensemble with GroupKFold at BOTH levels — most stringent."""
    gkf = GroupKFold(n_splits=n_splits)
    final_oof = np.zeros(len(y), dtype=np.float64)
    for outer_train, outer_test in gkf.split(X, y, groups=groups):
        # Level 1: fit each model on outer_train, get L1 predictions on outer_test
        L1_test = np.zeros((len(outer_test), len(base_ctors)), dtype=np.float64)
        for m_idx, ctor in enumerate(base_ctors):
            model = ctor()
            model.fit(X[outer_train], y[outer_train])
            L1_test[:, m_idx] = model.predict_proba(X[outer_test])[:, 1]
        # Level 2: get L1 OOF predictions within outer_train (inner CV)
        L1_train_oof = np.zeros((len(outer_train), len(base_ctors)), dtype=np.float64)
        inner_gkf = GroupKFold(n_splits=3)
        inner_groups = groups[outer_train]
        for m_idx, ctor in enumerate(base_ctors):
            for i_tr, i_te in inner_gkf.split(X[outer_train], y[outer_train],
                                              groups=inner_groups):
                model = ctor()
                model.fit(X[outer_train][i_tr], y[outer_train][i_tr])
                L1_train_oof[i_te, m_idx] = model.predict_proba(X[outer_train][i_te])[:, 1]
        meta = LogisticRegression(penalty="l2", C=1.0, max_iter=500)
        meta.fit(L1_train_oof, y[outer_train])
        final_oof[outer_test] = meta.predict_proba(L1_test)[:, 1]
    return final_oof


def main():
    conn = psycopg2.connect(DB_URL)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(_ph1.PHASE1_SQL)
        rows = cur.fetchall()
    conn.close()

    X = np.stack([_ml.row_to_feature_vector(r) for r in rows])
    y = np.array([1 if r["y_strict"] else 0 for r in rows], dtype=np.int64)
    groups = np.array([r["target_id"] for r in rows], dtype=np.int64)
    X_log = _stack.log_transform_features(X, _ml.FEATURE_NAMES)

    print(f"Phase 1+ strict cohort: n={len(rows)}, pos_rate={y.mean():.4f}, "
          f"unique targets={len(set(groups))}")

    base = [_stack.make_logreg_l2, _robust.make_lgb_robust, _ml.make_rf]
    print(f"\n== Stacked (held-out-target GroupKFold, Phase 1+, strict) ==")
    oof = group_stacked_cv(X_log, y, groups, base)
    _robust.eval_and_store(
        "stacked_final_no_nelson_v1", oof, y,
        "ti_phase1plus_strict_holdout_target_no_nelson",
        "Phase 1+ strict cohort, GroupKFold on target_id, stacked LogReg + "
        "robust-LGB + RF meta ensemble, log-transformed counts; nelson_tier excluded",
    )

    # LogReg alone with GroupKFold
    print(f"\n== LogReg alone (held-out-target GroupKFold, Phase 1+, strict) ==")
    gkf = GroupKFold(n_splits=5)
    oof2 = np.zeros(len(y), dtype=np.float64)
    for tr, te in gkf.split(X_log, y, groups=groups):
        model = _stack.make_logreg_l2()
        model.fit(X_log[tr], y[tr])
        oof2[te] = model.predict_proba(X_log[te])[:, 1]
    _robust.eval_and_store(
        "logreg_final_no_nelson_v1", oof2, y,
        "ti_phase1plus_strict_holdout_target_no_nelson",
        "LogReg L2, GroupKFold(target), Phase 1+ strict, log-transformed; "
        "nelson_tier excluded",
    )


if __name__ == "__main__":
    main()
