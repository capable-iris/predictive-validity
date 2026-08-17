"""Max hill-climb: XGBoost + CatBoost + calibrated wrappers + interaction features.

Uses held-out-target GroupKFold on Phase 1+ strict — the hardest benchmark.
Also adds interaction features between top signals.
"""

import os
import sys
from datetime import date
import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor
from sklearn.model_selection import GroupKFold
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

import xgboost as xgb
import catboost as cb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'benchmark'))
from importlib import import_module
_runner = import_module("runner")
_ml = import_module("scorers_ml")
_robust = import_module("scorers_ml")
_stack = import_module("scorers_ensemble")
_ph1 = import_module("phase1_cohort")
_final = import_module("final_benchmark")

DB_URL = os.environ["DATABASE_URL"]


def make_xgb():
    return xgb.XGBClassifier(
        n_estimators=300, learning_rate=0.05, max_depth=4,
        min_child_weight=10, subsample=0.7, colsample_bytree=0.6,
        reg_alpha=1.0, reg_lambda=1.0,
        scale_pos_weight=15.0,  # rough class balance
        eval_metric="logloss", random_state=42, verbosity=0,
    )


def make_catboost():
    return cb.CatBoostClassifier(
        iterations=300, learning_rate=0.05, depth=4,
        l2_leaf_reg=3.0, random_seed=42,
        auto_class_weights="Balanced",
        verbose=False,
    )


def add_interaction_features(X, feature_names):
    """Add hand-crafted interaction features."""
    n = X.shape[0]
    # Feature index lookup
    idx = {name: i for i, name in enumerate(feature_names)}
    interactions = []
    interaction_names = []

    # Genetics × tissue-specificity (Pheiron finding)
    if "mendelian_n" in idx and "sc_tau_specificity" in idx:
        mask = np.nan_to_num(X[:, idx["mendelian_n"]] > 4).astype(float) * \
               np.nan_to_num(X[:, idx["sc_tau_specificity"]] > 0.75).astype(float)
        interactions.append(mask)
        interaction_names.append("mendelian_x_tissue_specific")

    # ClinGen × tractability
    if "clingen_n_strong" in idx and "tractability_sm" in idx:
        mask = np.nan_to_num(X[:, idx["clingen_n_strong"]] > 0).astype(float) * \
               np.nan_to_num(X[:, idx["tractability_sm"]] > 0.5).astype(float)
        interactions.append(mask)
        interaction_names.append("clingen_x_tractable_sm")

    # NOT-pan-essential × strong genetics (safe drug target)
    if "depmap_pan_essential" in idx and "clingen_n_strong" in idx:
        mask = (1 - np.nan_to_num(X[:, idx["depmap_pan_essential"]])).astype(float) * \
               np.nan_to_num(X[:, idx["clingen_n_strong"]] > 0).astype(float)
        interactions.append(mask)
        interaction_names.append("nonessential_x_clingen")

    # OT genetic × animal model (Pheiron combo)
    if "ot_genetic_max" in idx and "ot_animal_model_max" in idx:
        mask = np.nan_to_num(X[:, idx["ot_genetic_max"]] > 0.3).astype(float) * \
               np.nan_to_num(X[:, idx["ot_animal_model_max"]] > 0.3).astype(float)
        interactions.append(mask)
        interaction_names.append("ot_genetic_x_animal")

    # Oncology × DepMap-dependent (cell essentiality matters more in oncology)
    if "ta_oncology" in idx and "depmap_n_dep_lineages" in idx:
        mask = np.nan_to_num(X[:, idx["ta_oncology"]]).astype(float) * \
               np.log1p(np.nan_to_num(X[:, idx["depmap_n_dep_lineages"]]))
        interactions.append(mask)
        interaction_names.append("oncology_x_depmap_deps")

    if not interactions:
        return X, feature_names
    X_ext = np.hstack([X, np.column_stack(interactions)])
    return X_ext, feature_names + interaction_names


def eval_model(name, model_ctor, X, y, groups, cohort_def, notes):
    print(f"\n== {name} (GroupKFold on target, Phase 1+ strict) ==")
    gkf = GroupKFold(n_splits=5)
    oof = np.zeros(len(y), dtype=np.float64)
    for tr, te in gkf.split(X, y, groups=groups):
        model = model_ctor()
        model.fit(X[tr], y[tr])
        oof[te] = model.predict_proba(X[te])[:, 1]

    y_list, p_list = y.tolist(), oof.tolist()
    auc, auc_lo, auc_hi = _runner.bootstrap_metric(y_list, p_list, _runner.auc_roc)
    brier = _runner.brier_score(y_list, p_list)
    r10 = _runner.recall_at_top_k(y_list, p_list, 0.10)
    p10 = _runner.precision_at_top_k(y_list, p_list, 0.10)
    rs10 = _runner.rs_by_top_decile(y_list, p_list)
    ece = _runner.calibration_ece(y_list, p_list)
    print(f"  AUC = {auc:.3f} [{auc_lo:.3f}, {auc_hi:.3f}]")
    print(f"  Brier = {brier:.3f}, R@10% = {r10:.3f}, P@10% = {p10:.3f}, RS10 = {rs10:.2f}, ECE = {ece:.3f}")
    _robust.eval_and_store(name, oof, y, cohort_def, notes,
                           scoring_version="v2_hpo")
    return oof


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
    X_ext, feat_ext = add_interaction_features(X_log, _ml.FEATURE_NAMES)
    print(f"Cohort: n={len(rows)}, pos_rate={y.mean():.4f}, features={X_ext.shape[1]}")

    # Fill NaN for tree models that don't handle them (XGBoost handles, CatBoost handles too)
    X_ext_filled = np.nan_to_num(X_ext, nan=0.0)

    eval_model("xgboost_final_v2_hpo", make_xgb, X_ext, y, groups,
               "ti_phase1plus_strict_holdout_target",
               "XGBoost 300 trees, depth 4, on Phase 1+ strict + interactions, GroupKFold, corrected HPO")

    eval_model("catboost_final_v2_hpo", make_catboost, X_ext_filled, y, groups,
               "ti_phase1plus_strict_holdout_target",
               "CatBoost 300 iter, depth 4, on Phase 1+ strict + interactions, GroupKFold, corrected HPO")

    # Recheck logreg with interactions
    eval_model("logreg_interactions_v2_hpo", _stack.make_logreg_l2, X_ext_filled, y, groups,
               "ti_phase1plus_strict_holdout_target",
               "LogReg L2 + interactions, Phase 1+ strict, GroupKFold, corrected HPO")


if __name__ == "__main__":
    main()
