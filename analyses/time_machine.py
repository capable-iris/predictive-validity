"""Time-machine on STRICT outcome with time-cutoff-aware precedent.

Combines fixes from 21 + 22:
- Uses strict per-T-I approval (approved for THIS indication)
- Computes family/gene precedent as-of first_trial_date via join to
  v_target_family_precedent_by_year — but joins in Python to avoid slow SQL
  CROSS JOIN.
"""

import os
import sys
from datetime import date

import numpy as np
import psycopg2
from psycopg2.extras import execute_values, RealDictCursor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'benchmark'))
from importlib import import_module
_runner = import_module("runner")
_ml = import_module("scorers_ml")

DB_URL = os.environ["DATABASE_URL"]


COHORT_SQL = """
    SELECT
      s.target_id, s.indication_id,
      s.strict_approved_this_ti AS y_strict,
      s.first_trial_date, s.max_phase_reached,
      s.n_programs, s.n_sponsors, s.outcomes_broad_all,
      t.symbol AS target_symbol, i.display_name AS indication_name,
      i.therapeutic_area,
      tw.*
    FROM preclin.v_target_indication_strict_outcome s
    JOIN public.targets t ON t.id = s.target_id
    JOIN preclin.indication i ON i.indication_id = s.indication_id
    JOIN preclin.v_target_evidence_wide tw ON tw.target_id = s.target_id
    WHERE s.max_phase_reached >= 2
      AND s.first_trial_date IS NOT NULL
      AND (t.pathogen_type IS NULL OR t.pathogen_type = '')
      AND s.outcomes_broad_all NOT SIMILAR TO 'in_dev%%'
"""


def load_precedent_table(conn):
    """Load family precedent as-of year table into a dict."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT target_id, year, family_approved_before_year, gene_approved_before_year "
                    "FROM preclin.v_target_family_precedent_by_year")
        d = {}
        for r in cur.fetchall():
            d[(r["target_id"], r["year"])] = (int(r["family_approved_before_year"]),
                                               int(r["gene_approved_before_year"]))
    return d


def row_to_features_strict(row, precedent_dict):
    """Feature vector with time-cutoff-aware family precedent."""
    year = row["first_trial_date"].year if row["first_trial_date"] else 2020
    fam_before, gene_before = precedent_dict.get((row["target_id"], year), (0, 0))

    feats = []
    for f in _ml.NUMERIC_FEATURES:
        if f == "family_approved_count":
            v = fam_before
        elif f == "gene_approved_count":
            v = gene_before
        else:
            v = row.get(f)
        if v is None:
            feats.append(np.nan)
        else:
            try:
                feats.append(float(v))
            except (TypeError, ValueError):
                feats.append(np.nan)
    for f in _ml.BOOL_FEATURES:
        v = row.get(f)
        feats.append(np.nan if v is None else (1.0 if v else 0.0))
    for a in _ml.THERAPEUTIC_AREAS:
        feats.append(1.0 if (row.get("therapeutic_area") or "other") == a else 0.0)
    return np.array(feats, dtype=np.float64)


def run(cutoff: date, scorer_name="lightgbm_strict_timemachine_v1", model_ctor=_ml.make_lgb):
    print(f"\n== Strict Time-Machine: cutoff={cutoff}, {scorer_name} ==")
    conn = psycopg2.connect(DB_URL)
    precedent = load_precedent_table(conn)
    print(f"  precedent lookup: {len(precedent)} entries")

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(COHORT_SQL)
        rows = cur.fetchall()
    print(f"  cohort n = {len(rows)}")

    train = [r for r in rows if r["first_trial_date"] < cutoff]
    test = [r for r in rows if r["first_trial_date"] >= cutoff]
    print(f"  train n = {len(train)}, test n = {len(test)}")

    X_train = np.stack([row_to_features_strict(r, precedent) for r in train])
    y_train = np.array([1 if r["y_strict"] else 0 for r in train], dtype=np.int64)
    X_test  = np.stack([row_to_features_strict(r, precedent) for r in test])
    y_test  = np.array([1 if r["y_strict"] else 0 for r in test], dtype=np.int64)

    print(f"  train pos rate: {y_train.mean():.4f}, test pos rate: {y_test.mean():.4f}")

    if y_train.sum() < 5 or y_test.sum() < 5:
        print("  Insufficient positives in train or test. Skipping.")
        conn.close()
        return

    model = model_ctor()
    model.fit(X_train, y_train)
    p_test = model.predict_proba(X_test)[:, 1]

    y_list, p_list = y_test.tolist(), p_test.tolist()
    auc, auc_lo, auc_hi = _runner.bootstrap_metric(y_list, p_list, _runner.auc_roc)
    brier = _runner.brier_score(y_list, p_list)
    r10 = _runner.recall_at_top_k(y_list, p_list, 0.10)
    p10 = _runner.precision_at_top_k(y_list, p_list, 0.10)
    rs10 = _runner.rs_by_top_decile(y_list, p_list)
    ece = _runner.calibration_ece(y_list, p_list)

    print(f"  AUC = {auc:.3f} [{auc_lo:.3f}, {auc_hi:.3f}]")
    print(f"  Brier = {brier:.3f}, R@10% = {r10:.3f}, P@10% = {p10:.3f}, RS10 = {rs10:.2f}, ECE = {ece:.3f}")

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO preclin.benchmark_run
              (scoring_function, scoring_version, cutoff_date, cohort_definition,
               n_ti_pairs, n_approved, n_failed,
               auc_roc, auc_roc_ci_lo, auc_roc_ci_hi, brier_score,
               recall_at_10pct, precision_at_10pct, rs_top_decile,
               calibration_ece, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (scorer_name, "v3_strict_time_cutoff", cutoff,
              f"ti_phase2plus_strict_post_{cutoff.year}", len(y_test),
              int(y_test.sum()), int(len(y_test) - y_test.sum()),
              auc, auc_lo, auc_hi, brier, r10, p10, rs10, ece,
              f"STRICT per-T-I outcome + time-cutoff family precedent, "
              f"cutoff={cutoff}, train n={len(train)}"))
        conn.commit()
    conn.close()


if __name__ == "__main__":
    for cutoff in [date(2017, 1, 1), date(2019, 1, 1), date(2021, 1, 1)]:
        run(cutoff, "lightgbm_strict_timemachine_v1", _ml.make_lgb)
        run(cutoff, "logreg_strict_timemachine_v1", _ml.make_logreg)
