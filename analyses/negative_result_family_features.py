"""Adds target-family one-hot features + family × genetics interactions.

Rationale: kinase inhibitors, GPCR modulators, nuclear receptor drugs have
very different clinical trajectories. Adding family info may help the linear
model separate these regimes.
"""

import os
import sys
import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor
from sklearn.model_selection import GroupKFold
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'benchmark'))
from importlib import import_module
_runner = import_module("runner")
_ml = import_module("scorers_ml")
_robust = import_module("scorers_ml")
_stack = import_module("scorers_ensemble")
_ph1 = import_module("phase1_cohort")

DB_URL = os.environ["DATABASE_URL"]

FAMILIES = ["Enzyme", "Kinase", "GPCR", "Nuclear Receptor", "Ion Channel",
            "Transporter", "Transcription Factor", "Epigenetic"]


PHASE1_WITH_FAMILY = _ph1.PHASE1_SQL.replace(
    "tw.*,",
    "tw.*, t.family AS target_family, t.tdl AS target_tdl,"
)


def row_to_features_with_family(row):
    """Standard features + target family one-hot + family x genetics interactions."""
    base = _ml.row_to_feature_vector(row)
    feats = list(base)
    # Family one-hot
    fam = row.get("target_family") or "other"
    for f in FAMILIES:
        feats.append(1.0 if fam == f else 0.0)
    # TDL one-hot
    tdl = row.get("target_tdl") or "Tdark"
    for t in ["Tclin", "Tchem", "Tbio", "Tdark"]:
        feats.append(1.0 if tdl == t else 0.0)
    # Family × ClinGen (strong genetics + specific family)
    for f in FAMILIES:
        is_fam = 1.0 if fam == f else 0.0
        clingen_str = 1.0 if row.get("clingen_n_strong") and row["clingen_n_strong"] >= 1 else 0.0
        feats.append(is_fam * clingen_str)
    return np.array(feats, dtype=np.float64)


FEATURE_NAMES_EXT = (
    _ml.FEATURE_NAMES +
    [f"fam_{f}" for f in FAMILIES] +
    [f"tdl_{t}" for t in ["Tclin", "Tchem", "Tbio", "Tdark"]] +
    [f"{f}_x_clingen" for f in FAMILIES]
)


def main():
    conn = psycopg2.connect(DB_URL)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(PHASE1_WITH_FAMILY)
        rows = cur.fetchall()
    conn.close()

    X = np.stack([row_to_features_with_family(r) for r in rows])
    y = np.array([1 if r["y_strict"] else 0 for r in rows], dtype=np.int64)
    groups = np.array([r["target_id"] for r in rows], dtype=np.int64)
    # Log-transform count features
    X_log = _stack.log_transform_features(X, FEATURE_NAMES_EXT)

    print(f"Cohort: n={len(rows)}, pos_rate={y.mean():.4f}, features={X.shape[1]}")

    # Held-out-target CV
    gkf = GroupKFold(n_splits=5)
    oof = np.zeros(len(y), dtype=np.float64)
    for tr, te in gkf.split(X_log, y, groups=groups):
        model = _stack.make_logreg_l2()
        model.fit(X_log[tr], y[tr])
        oof[te] = model.predict_proba(X_log[te])[:, 1]

    y_list, p_list = y.tolist(), oof.tolist()
    auc, auc_lo, auc_hi = _runner.bootstrap_metric(y_list, p_list, _runner.auc_roc)
    brier = _runner.brier_score(y_list, p_list)
    r10 = _runner.recall_at_top_k(y_list, p_list, 0.10)
    p10 = _runner.precision_at_top_k(y_list, p_list, 0.10)
    rs10 = _runner.rs_by_top_decile(y_list, p_list)
    ece = _runner.calibration_ece(y_list, p_list)
    print(f"\n== LogReg + family features (Phase 1+ strict held-out-target) ==")
    print(f"AUC = {auc:.3f} [{auc_lo:.3f}, {auc_hi:.3f}]")
    print(f"Brier = {brier:.3f}, R@10% = {r10:.3f}, P@10% = {p10:.3f}, RS10 = {rs10:.2f}, ECE = {ece:.3f}")

    _robust.eval_and_store("logreg_family_v2_hpo", oof, y,
                            "ti_phase1plus_strict_holdout_target",
                            "LogReg + target-family one-hot + family x ClinGen interactions, "
                            "Phase 1+ strict, GroupKFold on target, corrected HPO",
                            scoring_version="v2_hpo")

    # Now the stacked version — use standard LGB without monotonic constraints
    # (they'd need to match new feature dim)
    print(f"\n== Stacked with family features ==")

    import lightgbm as lgb
    def make_lgb_simple():
        return lgb.LGBMClassifier(
            n_estimators=200, learning_rate=0.03, max_depth=4, num_leaves=15,
            min_child_samples=50, subsample=0.7, colsample_bytree=0.6,
            reg_alpha=1.0, reg_lambda=1.0, class_weight="balanced",
            random_state=42, verbose=-1)

    def stacked_predict():
        base = [_stack.make_logreg_l2, make_lgb_simple, _ml.make_rf]
        gkf_outer = GroupKFold(n_splits=5)
        final = np.zeros(len(y))
        for tr, te in gkf_outer.split(X_log, y, groups=groups):
            L1_test = np.zeros((len(te), len(base)))
            for m, ctor in enumerate(base):
                model = ctor(); model.fit(X_log[tr], y[tr])
                L1_test[:, m] = model.predict_proba(X_log[te])[:, 1]
            # Inner CV for L1_train
            L1_train = np.zeros((len(tr), len(base)))
            inner = GroupKFold(n_splits=3)
            for m, ctor in enumerate(base):
                for i_tr, i_te in inner.split(X_log[tr], y[tr], groups=groups[tr]):
                    model = ctor(); model.fit(X_log[tr][i_tr], y[tr][i_tr])
                    L1_train[i_te, m] = model.predict_proba(X_log[tr][i_te])[:, 1]
            meta = LogisticRegression(penalty="l2", C=1.0, max_iter=500)
            meta.fit(L1_train, y[tr])
            final[te] = meta.predict_proba(L1_test)[:, 1]
        return final

    oof2 = stacked_predict()
    y_list, p_list = y.tolist(), oof2.tolist()
    auc, auc_lo, auc_hi = _runner.bootstrap_metric(y_list, p_list, _runner.auc_roc)
    brier = _runner.brier_score(y_list, p_list)
    r10 = _runner.recall_at_top_k(y_list, p_list, 0.10)
    p10 = _runner.precision_at_top_k(y_list, p_list, 0.10)
    rs10 = _runner.rs_by_top_decile(y_list, p_list)
    ece = _runner.calibration_ece(y_list, p_list)
    print(f"AUC = {auc:.3f} [{auc_lo:.3f}, {auc_hi:.3f}]")
    print(f"Brier = {brier:.3f}, R@10% = {r10:.3f}, P@10% = {p10:.3f}, RS10 = {rs10:.2f}, ECE = {ece:.3f}")

    _robust.eval_and_store("stacked_family_v2_hpo", oof2, y,
                            "ti_phase1plus_strict_holdout_target",
                            "Stacked ensemble with family features. Held-out-target Ph1+ strict, corrected HPO.",
                            scoring_version="v2_hpo")


if __name__ == "__main__":
    main()
