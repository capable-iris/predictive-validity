"""Per-modality analysis on strict outcome.

Does the model predict small-molecule programs better than biologics?
Does the ranking generalize across modalities?
"""

import os
import sys
from collections import Counter
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


# Join in the primary drug's modality for each T-I pair (majority modality of programs)
PER_MODALITY_SQL = """
    WITH ti_modality AS (
      SELECT dt.target_id, p.indication_id,
        MODE() WITHIN GROUP (ORDER BY d.modality) AS primary_modality,
        COUNT(*) AS n_progs
      FROM preclin.program p
      JOIN preclin.drug d ON d.drug_id = p.drug_id
      JOIN preclin.v_drug_target dt ON dt.drug_id = p.drug_id AND dt.role='primary'
      WHERE d.is_placebo IS NOT TRUE
      GROUP BY dt.target_id, p.indication_id
    )
    SELECT s.target_id, s.indication_id,
      s.strict_approved_this_ti AS y_strict,
      s.first_trial_date, s.max_phase_reached,
      s.n_programs, s.n_sponsors,
      i.therapeutic_area,
      tim.primary_modality,
      tw.*
    FROM preclin.v_target_indication_strict_outcome s
    JOIN public.targets t ON t.id = s.target_id
    JOIN preclin.indication i ON i.indication_id = s.indication_id
    JOIN preclin.v_target_evidence_wide tw ON tw.target_id = s.target_id
    JOIN ti_modality tim ON tim.target_id = s.target_id AND tim.indication_id = s.indication_id
    WHERE s.max_phase_reached >= 2
      AND (t.pathogen_type IS NULL OR t.pathogen_type = '')
      AND s.outcomes_broad_all NOT SIMILAR TO 'in_dev%%'
"""


def main():
    conn = psycopg2.connect(DB_URL)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(PER_MODALITY_SQL)
        rows = cur.fetchall()
    conn.close()

    print(f"Total T-I with modality: {len(rows)}")
    modality_counts = Counter(r["primary_modality"] or "unknown" for r in rows)
    print("\nModality distribution:")
    for m, n in sorted(modality_counts.items(), key=lambda x: -x[1]):
        appr = sum(1 for r in rows if (r["primary_modality"] or "unknown") == m
                   and r["y_strict"])
        pct = 100 * appr / n
        print(f"  {m or 'unknown':<40} n={n:5} approved={appr:4} ({pct:.1f}%)")

    # Bucket modalities into small_molecule vs biologic vs other for cleaner stats
    def bucket(m):
        if not m:
            return "other"
        m = m.lower()
        if "small" in m or "molecule" in m:
            return "small_molecule"
        if any(k in m for k in ["antibody", "mab", "protein", "peptide", "biologic"]):
            return "biologic"
        if any(k in m for k in ["gene", "aso", "sirna", "rna"]):
            return "genetic_medicine"
        if any(k in m for k in ["cell", "car_t", "immunotherapy"]):
            return "cell_therapy"
        return "other"

    for r in rows:
        r["mod_bucket"] = bucket(r.get("primary_modality"))

    print("\nModality bucket distribution:")
    for m in ["small_molecule", "biologic", "genetic_medicine", "cell_therapy", "other"]:
        sub = [r for r in rows if r["mod_bucket"] == m]
        if not sub:
            continue
        appr = sum(1 for r in sub if r["y_strict"])
        pct = 100 * appr / len(sub)
        print(f"  {m:<20} n={len(sub):5} approved={appr:4} ({pct:.1f}%)")

    print("\n\nPer-modality LogReg (5-fold CV) — strict outcome:")
    for m in ["small_molecule", "biologic", "genetic_medicine", "other"]:
        sub = [r for r in rows if r["mod_bucket"] == m]
        if len(sub) < 100:
            print(f"  {m:<20} n={len(sub)} SKIP (too small)")
            continue
        X = np.stack([_ml.row_to_feature_vector(r) for r in sub])
        y = np.array([1 if r["y_strict"] else 0 for r in sub], dtype=np.int64)
        if y.sum() < 5:
            print(f"  {m:<20} SKIP (< 5 positives)")
            continue
        X_log = _stack.log_transform_features(X, _ml.FEATURE_NAMES)
        oof = _robust.cv_predict_strict(_stack.make_logreg_l2, X_log, y,
                                         n_splits=min(5, int(y.sum())))
        auc, auc_lo, auc_hi = _runner.bootstrap_metric(y.tolist(), oof.tolist(), _runner.auc_roc)
        rs10 = _runner.rs_by_top_decile(y.tolist(), oof.tolist())
        r10 = _runner.recall_at_top_k(y.tolist(), oof.tolist(), 0.10)
        p10 = _runner.precision_at_top_k(y.tolist(), oof.tolist(), 0.10)
        print(f"  {m:<20} n={len(sub):5} appr={int(y.sum()):4}  AUC={auc:.3f} [{auc_lo:.3f}, {auc_hi:.3f}]  RS10={rs10:.2f}  R@10={r10:.2f} P@10={p10:.2f}")

        conn = psycopg2.connect(DB_URL)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO preclin.benchmark_run
                  (scoring_function, scoring_version, cohort_definition,
                   n_ti_pairs, n_approved, n_failed, auc_roc, auc_roc_ci_lo, auc_roc_ci_hi,
                   rs_top_decile, recall_at_10pct, precision_at_10pct, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (f"logreg_strict_{m}_v1", "v3_strict",
                  f"ti_phase2plus_strict_modality_{m}", len(sub), int(y.sum()),
                  int(len(sub) - y.sum()),
                  auc, auc_lo, auc_hi, rs10, r10, p10,
                  f"Per-modality LogReg 5-fold CV OOF on strict outcome, modality={m}"))
            conn.commit()
        conn.close()


if __name__ == "__main__":
    main()
