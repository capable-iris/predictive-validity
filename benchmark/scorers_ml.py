"""ML-trained scorers with 5-fold CV OOF evaluation.

Provides model factories + feature engineering + a CV driver. Used by:
- analyses/final_benchmark.py (the honest final benchmark)
- analyses/phase1_cohort.py
- analyses/held_out_target.py
- analyses/ablation.py
- analyses/time_machine.py

Models:
- LogReg (L2, isotonic-calibrated) — best on strict outcome
- LightGBM (unregularized) — overfits on strict
- LightGBM robust (monotonic constraints + regularization)
- LightGBM w/ isotonic post-calibration
- RandomForest

Features: 40+ evidence dimensions from v_target_evidence_wide + T-I context
(Nelson tier one-hot, therapeutic area one-hot). Deliberately EXCLUDES leaky
post-outcome features: n_sponsors, n_programs, max_phase_reached,
ot_known_drug_max, ot_overall_max.
"""

import os
import sys
import warnings

import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

try:
    import lightgbm as lgb
    HAS_LGB = True
except ImportError:
    HAS_LGB = False

warnings.filterwarnings("ignore")
np.random.seed(42)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib import import_module
_runner = import_module("runner")

DB_URL = os.environ["DATABASE_URL"]


# ============================================================
# Feature engineering — pre-outcome features only.
# ============================================================
# Explicitly excluded (LEAKY):
#   n_sponsors / n_programs / n_drugs / max_phase_reached — post-hoc program development
#   ot_known_drug_max — Open Targets score derived from known approved drugs
#   ot_overall_max — includes ot_known_drug_max as a component

NUMERIC_FEATURES = [
    # A. Genetics
    "mendelian_n", "mendelian_n_dominant", "mendelian_n_recessive",
    "clingen_n_strong", "gwas_n_sig", "n_hpo_phenotypes",
    "ot_genetic_max", "ot_somatic_score_max", "ot_rna_expression_max",
    "ot_l2g_score_max",
    # B. Mechanistic
    "line_b_lit", "tau_specificity", "sc_tau_specificity",
    "n_ppi_partners", "n_reactome_pathways",
    "n_go_biological_process", "n_go_molecular_function", "n_go_cellular_component",
    "max_tissue_tpm", "n_high_tissues", "sc_max_cell_value", "sc_n_cell_types_expressed",
    # C. Cell
    "line_c_lit", "depmap_n_dep_lineages", "depmap_mean_effect",
    # D. Animal
    "line_d_lit", "ot_animal_model_max", "impc_n_phenotypes",
    # E. Human PD
    "line_e_lit",
    # H. Safety
    "gnomad_pli", "gnomad_loeuf",
    # I. Landscape
    "family_approved_count", "gene_approved_count",
    "n_causal_diseases", "n_suggestive_diseases", "n_dgidb_drugs",
]

BOOL_FEATURES = [
    "tractability_sm", "tractability_ab", "tractability_protac",
    "depmap_pan_essential", "ot_is_mendelian_any",
]

NELSON_TIERS = ["T0", "T1", "T2", "T3", "T4"]
THERAPEUTIC_AREAS = ["oncology", "neuro", "autoimmune", "cv", "metabolic",
                     "rare", "infectious", "other"]

FEATURE_NAMES = (NUMERIC_FEATURES + BOOL_FEATURES +
                 [f"nelson_{t}" for t in NELSON_TIERS] +
                 [f"ta_{a}" for a in THERAPEUTIC_AREAS])

# Canonical evidence category for category-ablation and reporting. Nelson
# one-hot features are assigned below by prefix because they are generated.
FEATURE_CATEGORIES = {
    # A. Human genetics
    "mendelian_n": "A_genetics",
    "mendelian_n_dominant": "A_genetics",
    "mendelian_n_recessive": "A_genetics",
    "clingen_n_strong": "A_genetics",
    "gwas_n_sig": "A_genetics",
    "n_hpo_phenotypes": "A_genetics",
    "ot_genetic_max": "A_genetics",
    "ot_somatic_score_max": "A_genetics",
    "ot_rna_expression_max": "A_genetics",
    "ot_l2g_score_max": "A_genetics",
    "ot_is_mendelian_any": "A_genetics",
    # B. Mechanistic
    "line_b_lit": "B_mechanistic",
    "tau_specificity": "B_mechanistic",
    "sc_tau_specificity": "B_mechanistic",
    "n_ppi_partners": "B_mechanistic",
    "n_reactome_pathways": "B_mechanistic",
    "n_go_biological_process": "B_mechanistic",
    "n_go_molecular_function": "B_mechanistic",
    "n_go_cellular_component": "B_mechanistic",
    "max_tissue_tpm": "B_mechanistic",
    "n_high_tissues": "B_mechanistic",
    "sc_max_cell_value": "B_mechanistic",
    "sc_n_cell_types_expressed": "B_mechanistic",
    "tractability_sm": "B_mechanistic",
    "tractability_ab": "B_mechanistic",
    "tractability_protac": "B_mechanistic",
    # C. Cell
    "line_c_lit": "C_cell",
    "depmap_n_dep_lineages": "C_cell",
    "depmap_mean_effect": "C_cell",
    "depmap_pan_essential": "C_cell",
    # D. Animal
    "line_d_lit": "D_animal",
    "ot_animal_model_max": "D_animal",
    "impc_n_phenotypes": "D_animal",
    # E. Human PD
    "line_e_lit": "E_pd",
    # H. Safety
    "gnomad_pli": "H_safety",
    "gnomad_loeuf": "H_safety",
    # I. Landscape
    "family_approved_count": "I_landscape",
    "gene_approved_count": "I_landscape",
    "n_causal_diseases": "I_landscape",
    "n_suggestive_diseases": "I_landscape",
    "n_dgidb_drugs": "I_landscape",
}


def row_to_feature_vector(row: dict) -> np.ndarray:
    """Turn a cohort row into a numeric feature vector."""
    feats = []
    for f in NUMERIC_FEATURES:
        v = row.get(f)
        if v is None:
            feats.append(np.nan)
        else:
            try:
                feats.append(float(v))
            except (TypeError, ValueError):
                feats.append(np.nan)
    for f in BOOL_FEATURES:
        v = row.get(f)
        feats.append(np.nan if v is None else (1.0 if v else 0.0))
    tier = row.get("nelson_tier")
    for t in NELSON_TIERS:
        feats.append(1.0 if tier == t else 0.0)
    ta = row.get("therapeutic_area") or "other"
    for a in THERAPEUTIC_AREAS:
        feats.append(1.0 if ta == a else 0.0)
    return np.array(feats, dtype=np.float64)


# ============================================================
# Model factories
# ============================================================

def make_logreg():
    """L2 LogReg with isotonic-calibrated wrapper. Best on strict outcome."""
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("clf", CalibratedClassifierCV(
            LogisticRegression(penalty="l2", C=1.0, max_iter=500, class_weight="balanced"),
            method="isotonic", cv=3)),
    ])


def make_logreg_l2():
    """L2 LogReg without extra calibration wrapper. Used in stacking."""
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(penalty="l2", C=0.5, max_iter=500,
                                    class_weight="balanced")),
    ])


def make_rf():
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("clf", RandomForestClassifier(
            n_estimators=200, max_depth=8, min_samples_leaf=10,
            random_state=42, n_jobs=-1, class_weight="balanced")),
    ])


def make_lgb():
    """Unregularized LightGBM — best in-fold, overfits out-of-time."""
    if not HAS_LGB:
        return None
    return lgb.LGBMClassifier(
        n_estimators=300, learning_rate=0.05, max_depth=6, num_leaves=31,
        min_child_samples=20, subsample=0.8, colsample_bytree=0.8,
        reg_alpha=0.1, reg_lambda=0.1, class_weight="balanced",
        random_state=42, verbose=-1,
    )


# Monotonic constraints: known-directional features.
MONO_CONSTRAINTS = {
    "mendelian_n": 1, "mendelian_n_dominant": 1, "mendelian_n_recessive": 1,
    "clingen_n_strong": 1, "ot_genetic_max": 1, "ot_somatic_score_max": 1,
    "family_approved_count": 1, "gene_approved_count": 1,
    "depmap_pan_essential": -1, "gnomad_pli": -1,
}


def make_lgb_robust():
    """Regularized LightGBM with monotonic constraints. Better out-of-time."""
    if not HAS_LGB:
        return None
    mc = [MONO_CONSTRAINTS.get(name, 0) for name in FEATURE_NAMES]
    return lgb.LGBMClassifier(
        n_estimators=200, learning_rate=0.03, max_depth=4, num_leaves=15,
        min_child_samples=50, subsample=0.7, colsample_bytree=0.6,
        reg_alpha=1.0, reg_lambda=1.0, class_weight="balanced",
        monotone_constraints=mc, monotone_constraints_method="advanced",
        random_state=42, verbose=-1,
    )


def make_lgb_isotonic():
    """Robust LightGBM + post-hoc isotonic calibration."""
    if not HAS_LGB:
        return None
    return CalibratedClassifierCV(
        lgb.LGBMClassifier(
            n_estimators=200, learning_rate=0.03, max_depth=4, num_leaves=15,
            min_child_samples=50, subsample=0.7, colsample_bytree=0.6,
            reg_alpha=1.0, reg_lambda=1.0, class_weight="balanced",
            random_state=42, verbose=-1),
        method="isotonic", cv=3,
    )


# ============================================================
# Feature transform + CV driver
# ============================================================

COUNT_FEATURES_FOR_LOG = {
    "gwas_n_sig", "mendelian_n", "mendelian_n_dominant", "mendelian_n_recessive",
    "family_approved_count", "gene_approved_count", "n_causal_diseases",
    "n_suggestive_diseases", "n_dgidb_drugs", "n_ppi_partners",
    "n_reactome_pathways", "n_go_biological_process", "n_go_molecular_function",
    "n_go_cellular_component", "impc_n_phenotypes", "n_hpo_phenotypes",
    "max_tissue_tpm", "sc_max_cell_value", "sc_n_cell_types_expressed",
    "n_high_tissues", "clingen_n_strong",
}


def log_transform_features(X, feature_names=None):
    """Log-transform known count features to reduce skew."""
    feature_names = feature_names or FEATURE_NAMES
    X_new = X.copy()
    for i, name in enumerate(feature_names):
        if name in COUNT_FEATURES_FOR_LOG:
            X_new[:, i] = np.log1p(np.maximum(0, np.nan_to_num(X_new[:, i], nan=0)))
    return X_new


def cv_predict(model_ctor, X, y, n_splits=5, seed=42):
    """Fit model via stratified k-fold, return OOF P(y=1) and per-fold AUCs."""
    kf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    oof = np.zeros(len(y), dtype=np.float64)
    fold_aucs = []
    for train_idx, test_idx in kf.split(X, y):
        model = model_ctor()
        model.fit(X[train_idx], y[train_idx])
        oof[test_idx] = model.predict_proba(X[test_idx])[:, 1]
        auc = _runner.auc_roc(y[test_idx].tolist(), oof[test_idx].tolist())
        fold_aucs.append(auc)
    return oof, fold_aucs


# ============================================================
# Cohort loaders
# ============================================================

STRICT_COHORT_SQL = """
    SELECT s.target_id, s.indication_id,
      s.strict_approved_this_ti AS y_strict,
      s.first_trial_date, s.max_phase_reached,
      s.n_programs, s.n_sponsors, s.outcomes_broad_all,
      t.symbol AS target_symbol, i.display_name AS indication_name,
      i.therapeutic_area, tw.*,
      (SELECT value_text FROM preclin.evidence_score
        WHERE subject_type='target_indication' AND subject_id = s.target_id
          AND subject_id2 = s.indication_id AND dimension = 'nelson_tier'
        LIMIT 1) AS nelson_tier
    FROM preclin.v_target_indication_strict_outcome s
    JOIN public.targets t ON t.id = s.target_id
    JOIN preclin.indication i ON i.indication_id = s.indication_id
    JOIN preclin.v_target_evidence_wide tw ON tw.target_id = s.target_id
    WHERE s.max_phase_reached >= 2
      AND (t.pathogen_type IS NULL OR t.pathogen_type = '')
      AND s.outcomes_broad_all NOT SIMILAR TO 'in_dev%%'
"""


def load_strict():
    conn = psycopg2.connect(DB_URL)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(STRICT_COHORT_SQL)
        rows = cur.fetchall()
    conn.close()
    return list(rows)


def load_cohort_features(cohort_loader=load_strict, label_key="y_strict"):
    """Returns (raw_rows, X_features, y_labels)."""
    rows = cohort_loader()
    X = np.stack([row_to_feature_vector(r) for r in rows])
    y = np.array([1 if r.get(label_key) else 0 for r in rows], dtype=np.int64)
    return list(rows), X, y


# ============================================================
# Runner (for standalone `python3 scorers_ml.py`)
# ============================================================

def eval_and_store(scorer_name, oof, y, cohort_def, notes, scoring_version="v1"):
    from psycopg2.extras import execute_values
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
        """, (scorer_name, scoring_version, cohort_def, len(y),
              int(y.sum()), int(len(y) - y.sum()),
              auc, auc_lo, auc_hi, brier, r10, p10, rs10, ece, notes))
        conn.commit()
    conn.close()


if __name__ == "__main__":
    rows, X, y = load_cohort_features()
    print(f"Strict Phase 2+ cohort: n={len(rows)}, pos_rate={y.mean():.4f}")
    print("\n== logreg_strict_v1 ==")
    oof, _ = cv_predict(make_logreg, X, y)
    eval_and_store("logreg_strict_v1", oof, y, "ti_phase2plus_strict",
                    "LogReg L2 isotonic-calibrated on strict outcome")
    if HAS_LGB:
        print("\n== lightgbm_robust_strict_v1 ==")
        oof, _ = cv_predict(make_lgb_robust, X, y)
        eval_and_store("lightgbm_robust_strict_v1", oof, y, "ti_phase2plus_strict",
                        "Regularized LightGBM with monotonic constraints")
    print("\n== randomforest_strict_v1 ==")
    oof, _ = cv_predict(make_rf, X, y)
    eval_and_store("randomforest_strict_v1", oof, y, "ti_phase2plus_strict",
                    "RandomForest baseline")
