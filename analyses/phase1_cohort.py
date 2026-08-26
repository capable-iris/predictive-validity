"""Larger cohort: any Phase 1+ T-I pair with a resolved outcome.

Previously we filtered to Phase 2+ (max_phase_reached >= 2). But that's itself
a form of outcome filtering — drugs that fail at Phase 1 are excluded.

This variant uses Phase 1+ (max_phase_reached >= 1) — much larger, more
realistic base rate (closer to industry 90% clinical failure figure).

Task shape: "given a T-I hypothesis that entered clinical trials at all,
predict whether it will get FDA-approved specifically for this indication."
"""

import os
import sys
import numpy as np
import psycopg2
from psycopg2.extras import execute_values, RealDictCursor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'benchmark'))
from importlib import import_module
_runner = import_module("runner")
_ml = import_module("scorers_ml")
_robust = import_module("scorers_ml")
_stack = import_module("scorers_ensemble")

DB_URL = os.environ["DATABASE_URL"]


PHASE1_SQL = """
    SELECT s.target_id, s.indication_id,
      s.strict_approved_this_ti AS y_strict,
      s.first_trial_date, s.max_phase_reached,
      s.n_programs, s.n_sponsors,
      i.therapeutic_area, tw.*
    FROM preclin.v_target_indication_strict_outcome s
    JOIN public.targets t ON t.id = s.target_id
    JOIN preclin.indication i ON i.indication_id = s.indication_id
    JOIN preclin.v_target_evidence_wide tw ON tw.target_id = s.target_id
    WHERE s.max_phase_reached >= 1
      AND (t.pathogen_type IS NULL OR t.pathogen_type = '')
      AND s.outcomes_broad_all NOT SIMILAR TO 'in_dev%%'
    ORDER BY s.target_id, s.indication_id
"""


def main():
    conn = psycopg2.connect(DB_URL)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(PHASE1_SQL)
        rows = cur.fetchall()
    conn.close()

    X = np.stack([_ml.row_to_feature_vector(r) for r in rows])
    y = np.array([1 if r["y_strict"] else 0 for r in rows], dtype=np.int64)
    print(f"Phase 1+ strict cohort: n={len(rows)}, pos_rate={y.mean():.4f} ({int(y.sum())} approved)")

    X_log = _stack.log_transform_features(X, _ml.FEATURE_NAMES)

    # Run stacked v1 and LogReg on this larger cohort
    print("\n== stacked_ph1_strict_v1 ==")
    base = [_stack.make_logreg_l2, _robust.make_lgb_robust, _ml.make_rf]
    oof = _stack.stacked_cv_predict(X_log, y, base)
    y_list, p_list = y.tolist(), oof.tolist()
    auc, auc_lo, auc_hi = _runner.bootstrap_metric(y_list, p_list, _runner.auc_roc)
    r10 = _runner.recall_at_top_k(y_list, p_list, 0.10)
    p10 = _runner.precision_at_top_k(y_list, p_list, 0.10)
    rs10 = _runner.rs_by_top_decile(y_list, p_list)
    ece = _runner.calibration_ece(y_list, p_list)
    print(f"  AUC = {auc:.3f} [{auc_lo:.3f}, {auc_hi:.3f}]")
    print(f"  R@10% = {r10:.3f}, P@10% = {p10:.3f}, RS10 = {rs10:.2f}, ECE = {ece:.3f}")

    _robust.eval_and_store("stacked_ph1_strict_v1", oof, y, rows,
                            "ti_phase1plus_strict",
                            "Phase 1+ strict cohort, stacked ensemble")

    print("\n== logreg_ph1_strict_v1 ==")
    oof = _robust.cv_predict_strict(_stack.make_logreg_l2, X_log, y)
    y_list, p_list = y.tolist(), oof.tolist()
    auc, auc_lo, auc_hi = _runner.bootstrap_metric(y_list, p_list, _runner.auc_roc)
    r10 = _runner.recall_at_top_k(y_list, p_list, 0.10)
    p10 = _runner.precision_at_top_k(y_list, p_list, 0.10)
    rs10 = _runner.rs_by_top_decile(y_list, p_list)
    ece = _runner.calibration_ece(y_list, p_list)
    print(f"  AUC = {auc:.3f} [{auc_lo:.3f}, {auc_hi:.3f}]")
    print(f"  R@10% = {r10:.3f}, P@10% = {p10:.3f}, RS10 = {rs10:.2f}, ECE = {ece:.3f}")

    _robust.eval_and_store("logreg_ph1_strict_v1", oof, y, rows,
                            "ti_phase1plus_strict",
                            "Phase 1+ strict cohort, LogReg calibrated")


if __name__ == "__main__":
    main()
