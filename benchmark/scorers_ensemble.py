"""Stacked ensemble — meta-learner over the level-1 models.

Level 1 predictions (5-fold OOF) → level 2 LogReg meta-learner.
Prevents overfit via K-fold isolation at each level.

Also adds log-transformed count features (many count features are skewed).
"""

import os
import sys
import numpy as np
import psycopg2
from psycopg2.extras import execute_values, RealDictCursor
from sklearn.model_selection import StratifiedKFold
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.ensemble import RandomForestClassifier

try:
    import lightgbm as lgb
    HAS_LGB = True
except ImportError:
    HAS_LGB = False

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib import import_module
_runner = import_module("runner")
_ml = import_module("scorers_ml")
_robust = import_module("scorers_ml")

DB_URL = os.environ["DATABASE_URL"]


def log_transform_features(X, feature_names):
    """Log-transform observed counts while preserving unknown values."""
    COUNT_FEATURES = ["gwas_n_sig", "mendelian_n", "mendelian_n_dominant",
                      "mendelian_n_recessive", "family_approved_count",
                      "gene_approved_count", "n_causal_diseases",
                      "n_suggestive_diseases", "n_dgidb_drugs",
                      "n_ppi_partners", "n_reactome_pathways",
                      "n_go_biological_process", "n_go_molecular_function",
                      "n_go_cellular_component", "impc_n_phenotypes",
                      "n_hpo_phenotypes", "max_tissue_tpm",
                      "sc_max_cell_value", "sc_n_cell_types_expressed",
                      "n_high_tissues", "clingen_n_strong"]
    X_new = X.copy()
    for i, name in enumerate(feature_names):
        if name in COUNT_FEATURES:
            values = X_new[:, i]
            observed = np.isfinite(values)
            values[~observed] = np.nan
            values[observed] = np.log1p(np.maximum(0, values[observed]))
    return X_new


def make_logreg_l2():
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(penalty="l2", C=0.5, max_iter=500,
                                    class_weight="balanced")),
    ])


def get_level1_oof(X, y, model_ctors, n_splits=5):
    """Return matrix of shape (n_samples, n_models) — level-1 OOF predictions."""
    kf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
    L1 = np.zeros((len(y), len(model_ctors)), dtype=np.float64)
    for train_idx, test_idx in kf.split(X, y):
        for m_idx, ctor in enumerate(model_ctors):
            model = ctor()
            model.fit(X[train_idx], y[train_idx])
            L1[test_idx, m_idx] = model.predict_proba(X[test_idx])[:, 1]
    return L1


def stacked_cv_predict(X, y, base_ctors, n_splits=5):
    """Full stacked CV: level-1 OOF → level-2 LogReg OOF."""
    kf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
    final_oof = np.zeros(len(y), dtype=np.float64)
    for outer_train, outer_test in kf.split(X, y):
        # Level-1 CV within outer_train
        L1_train = get_level1_oof(X[outer_train], y[outer_train], base_ctors, n_splits=3)
        # Fit level-1 models on full outer_train, predict outer_test
        L1_test = np.zeros((len(outer_test), len(base_ctors)), dtype=np.float64)
        for m_idx, ctor in enumerate(base_ctors):
            model = ctor()
            model.fit(X[outer_train], y[outer_train])
            L1_test[:, m_idx] = model.predict_proba(X[outer_test])[:, 1]
        # Level-2 meta-learner
        meta = LogisticRegression(penalty="l2", C=1.0, max_iter=500)
        meta.fit(L1_train, y[outer_train])
        final_oof[outer_test] = meta.predict_proba(L1_test)[:, 1]
    return final_oof


def main():
    print("Loading strict cohort ...")
    rows = _robust.load_strict()
    X = np.stack([_ml.row_to_feature_vector(r) for r in rows])
    y = np.array([1 if r["y_strict"] else 0 for r in rows], dtype=np.int64)
    print(f"  n={len(rows)}, pos_rate={y.mean():.4f}")

    print("\nApplying log-transform to count features ...")
    X_log = log_transform_features(X, _ml.FEATURE_NAMES)

    base_ctors = [
        make_logreg_l2,
        _robust.make_lgb_robust,
        _ml.make_rf,
    ]

    print(f"\nRunning stacked ensemble ({len(base_ctors)} base models + LogReg meta) ...")
    oof = stacked_cv_predict(X_log, y, base_ctors)

    y_list, p_list = y.tolist(), oof.tolist()
    auc, auc_lo, auc_hi = _runner.bootstrap_metric(y_list, p_list, _runner.auc_roc)
    brier = _runner.brier_score(y_list, p_list)
    r10 = _runner.recall_at_top_k(y_list, p_list, 0.10)
    p10 = _runner.precision_at_top_k(y_list, p_list, 0.10)
    rs10 = _runner.rs_by_top_decile(y_list, p_list)
    ece = _runner.calibration_ece(y_list, p_list)
    print(f"\n== Stacked v1 (LogReg + robust-LGB + RF, log-transformed features) ==")
    print(f"AUC = {auc:.3f} [{auc_lo:.3f}, {auc_hi:.3f}]")
    print(f"Brier = {brier:.3f}, R@10% = {r10:.3f}, P@10% = {p10:.3f}, RS10 = {rs10:.2f}, ECE = {ece:.3f}")

    _robust.eval_and_store("stacked_v1", oof, y, rows,
                            "ti_phase2plus_strict",
                            "Level-1: LogReg + robust-LGB + RF; Level-2: LogReg meta on OOF; log-transformed counts.")


if __name__ == "__main__":
    main()
