"""Sanity-check: do LogReg's learned coefficients match published RS?

If the model works, features with high published Relative Success should get
positive weights, and features with low RS should get negative weights.
This is independent validation that the model captures real biology.

Compares LogReg (Phase 1+ strict) coefficients against
preclin.v_relative_success_clean.
"""

import os
import sys
import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'benchmark'))
from importlib import import_module
_ml = import_module("scorers_ml")
_robust = import_module("scorers_ml")
_stack = import_module("scorers_ensemble")
_ph1 = import_module("phase1_cohort")

DB_URL = os.environ["DATABASE_URL"]


def train_uncalibrated_logreg(X, y):
    """Fit uncalibrated LogReg to expose true coefficients (not wrapped in isotonic)."""
    pipe = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(penalty="l2", C=0.5, max_iter=500,
                                    class_weight="balanced")),
    ])
    pipe.fit(X, y)
    return pipe.named_steps["clf"].coef_[0]


def main():
    print("Loading Phase 1+ strict cohort ...")
    conn = psycopg2.connect(DB_URL)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(_ph1.PHASE1_SQL)
        rows = cur.fetchall()

    X = np.stack([_ml.row_to_feature_vector(r) for r in rows])
    y = np.array([1 if r["y_strict"] else 0 for r in rows], dtype=np.int64)
    X_log = _stack.log_transform_features(X, _ml.FEATURE_NAMES)
    print(f"  n={len(rows)}, pos_rate={y.mean():.4f}")

    print("\nFitting uncalibrated LogReg ...")
    coefs = train_uncalibrated_logreg(X_log, y)

    print("\nLoading published RS from preclin.v_relative_success_clean ...")
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT category, dimension, relative_success, n_supported
            FROM preclin.v_relative_success_clean
            WHERE relative_success IS NOT NULL AND n_supported >= 100
        """)
        rs_rows = cur.fetchall()
    conn.close()

    # Map dimension names to our feature names (rough match)
    RS_MAP = {
        "A. Mendelian ≥5": "mendelian_n",
        "A. ClinGen Strong/Def ≥1": "clingen_n_strong",
        "A. GWAS ≥50": "gwas_n_sig",
        "A. OT genetic ≥0.3": "ot_genetic_max",
        "A. OT somatic ≥0.3": "ot_somatic_score_max",
        "A. HPO phenotype breadth ≥10": "n_hpo_phenotypes",
        "B. Reactome pathways ≥5": "n_reactome_pathways",
        "B. PPI hub (≥50 partners)": "n_ppi_partners",
        "B. GO-BP ≥20 terms": "n_go_biological_process",
        "B. Bulk Tau ≥0.75": "tau_specificity",
        "B. SC Tau ≥0.75": "sc_tau_specificity",
        "B. Tractable — small mol": "tractability_sm",
        "B. Tractable — antibody": "tractability_ab",
        "C. Line C lit high (≥2)": "line_c_lit",
        "C. DepMap pan-essential": "depmap_pan_essential",
        "D. Line D lit high (≥2)": "line_d_lit",
        "D. OT animal model ≥0.3": "ot_animal_model_max",
        "D. IMPC ≥3 phenotypes": "impc_n_phenotypes",
        "E. Line E lit high (≥2)": "line_e_lit",
        "H. gnomAD pLI ≥0.9": "gnomad_pli",
        "H. gnomAD LOEUF <0.35": "gnomad_loeuf",
        "I. Causal disease pleiotropy ≥3": "n_causal_diseases",
        "I. DGIdb drug precedent ≥5": "n_dgidb_drugs",
    }

    coef_by_feature = dict(zip(_ml.FEATURE_NAMES, coefs))

    print("\n{:<38} {:>10} {:>10} {:>8}".format("Dimension", "RS_pub", "LogReg_β", "Sign_ok"))
    print("-" * 70)
    aligned, misaligned = 0, 0
    for r in rs_rows:
        feat = RS_MAP.get(r["dimension"])
        if not feat or feat not in coef_by_feature:
            continue
        rs = float(r["relative_success"])
        beta = coef_by_feature[feat]
        # Positive: RS > 1 AND beta > 0, OR RS < 1 AND beta < 0
        rs_positive = rs > 1.0
        beta_positive = beta > 0
        sign_ok = rs_positive == beta_positive
        if sign_ok:
            aligned += 1
        else:
            misaligned += 1
        mark = "✓" if sign_ok else "✗"
        print(f"{r['dimension']:<38} {rs:>10.2f} {beta:>+10.3f} {mark:>8}")

    print("-" * 70)
    total = aligned + misaligned
    print(f"\nAlignment: {aligned}/{total} = {100*aligned/total:.0f}%")

    # LogReg's own top-15 features
    print("\nLogReg top-15 positive coefficients:")
    top_pos = sorted(coef_by_feature.items(), key=lambda x: -x[1])[:15]
    for name, beta in top_pos:
        print(f"  {name:<38} β = {beta:+.3f}")

    print("\nLogReg top-15 negative coefficients:")
    top_neg = sorted(coef_by_feature.items(), key=lambda x: x[1])[:15]
    for name, beta in top_neg:
        print(f"  {name:<38} β = {beta:+.3f}")


if __name__ == "__main__":
    main()
