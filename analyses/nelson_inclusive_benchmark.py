"""Nelson-inclusive sensitivity benchmark on the headline Phase 1+ cohort.

It reruns both variants on the scientifically attributable cohort: non-placebo
programs with one uniquely best-supported primary-target consensus.
Both variants use identical 5-fold GroupKFold(target_id) splits. Nelson is an
ordered T0-T3 feature and complete adjudication is required; missing tiers are
never imputed as T0.
"""

from __future__ import annotations

import os
import sys
from urllib.parse import urlsplit, urlunsplit

import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "benchmark"))
from importlib import import_module

_final = import_module("final_benchmark")
_ml = import_module("scorers_ml")
_stack = import_module("scorers_ensemble")

DB_URL = os.environ["DATABASE_URL"]
TIER_VALUE = {"T0": 0.0, "T1": 1.0, "T2": 2.0, "T3": 3.0}
NELSON_FEATURE_NAMES = list(_ml.FEATURE_NAMES) + ["nelson_tier"]

NELSON_SQL = """
  SELECT DISTINCT ON (subject_id, subject_id2)
         subject_id AS target_id, subject_id2 AS indication_id,
         value_text AS nelson_tier
  FROM preclin.evidence_score
  WHERE subject_type = 'target_indication'
    AND dimension = 'nelson_tier'
    AND source = 'nelson_llm'
  ORDER BY subject_id, subject_id2, extracted_at DESC, evidence_id DESC
"""

PHASE1_CORE_SQL = """
SELECT s.target_id, s.indication_id,
  s.strict_approved_this_ti AS y_strict,
  s.first_trial_date, s.max_phase_reached,
  s.n_programs, s.n_sponsors,
  i.therapeutic_area
FROM preclin.v_target_indication_strict_outcome s
JOIN public.targets t ON t.id = s.target_id
JOIN preclin.indication i ON i.indication_id = s.indication_id
WHERE s.max_phase_reached >= 1
  AND (t.pathogen_type IS NULL OR t.pathogen_type = '')
  AND s.outcomes_broad_all NOT SIMILAR TO 'in_dev%%'
ORDER BY s.target_id, s.indication_id
"""


def encode_nelson_tiers(rows) -> np.ndarray:
    values = []
    for row in rows:
        tier = row.get("nelson_tier")
        if tier not in TIER_VALUE:
            raise ValueError(
                f"missing or invalid Nelson tier for "
                f"{row.get('target_id')}:{row.get('indication_id')}: {tier!r}"
            )
        values.append(TIER_VALUE[tier])
    return np.asarray(values, dtype=np.float64)


def grouped_auc_delta_ci(y, base, inclusive, groups, n_iter=1000, seed=42):
    """Target-cluster bootstrap CI for AUC(inclusive) - AUC(base)."""
    rng = np.random.default_rng(seed)
    unique_groups = np.unique(groups)
    by_group = {group: np.flatnonzero(groups == group) for group in unique_groups}
    deltas = []
    for _ in range(n_iter):
        sampled = rng.choice(unique_groups, size=len(unique_groups), replace=True)
        index = np.concatenate([by_group[group] for group in sampled])
        if np.unique(y[index]).size < 2:
            continue
        deltas.append(
            roc_auc_score(y[index], inclusive[index])
            - roc_auc_score(y[index], base[index])
        )
    point = roc_auc_score(y, inclusive) - roc_auc_score(y, base)
    lo, hi = np.quantile(deltas, [0.025, 0.975])
    return float(point), float(lo), float(hi)


def grouped_logreg_oof(X, y, groups):
    oof = np.zeros(len(y), dtype=np.float64)
    for train, test in GroupKFold(n_splits=5).split(X, y, groups=groups):
        model = _stack.make_logreg_l2()
        model.fit(X[train], y[train])
        oof[test] = model.predict_proba(X[test])[:, 1]
    return oof


def report_pair(label, y, groups, base, inclusive):
    base_auc = roc_auc_score(y, base)
    inclusive_auc = roc_auc_score(y, inclusive)
    delta, lo, hi = grouped_auc_delta_ci(y, base, inclusive, groups)
    print(
        f"{label}: base AUC={base_auc:.4f}, Nelson AUC={inclusive_auc:.4f}, "
        f"delta={delta:+.4f} (target-bootstrap 95% CI {lo:+.4f} to {hi:+.4f})"
    )


def main():
    parsed = urlsplit(DB_URL)
    cohort_url = DB_URL
    if "-pooler." in (parsed.hostname or ""):
        cohort_url = urlunsplit(
            (
                parsed.scheme,
                parsed.netloc.replace("-pooler.", ".", 1),
                parsed.path,
                parsed.query,
                parsed.fragment,
            )
        )
    conn = psycopg2.connect(
        cohort_url,
        connect_timeout=15,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
        options="-c statement_timeout=900000",
    )
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Load the established headline cohort and target-wide evidence in two
        # equivalent pieces. Avoiding the cross-view join keeps the remote
        # database plan small and makes socket failures recoverable.
        cur.execute(PHASE1_CORE_SQL)
        rows = [dict(row) for row in cur.fetchall()]
        target_ids = sorted({row["target_id"] for row in rows})
        cur.execute(
            "SELECT * FROM preclin.v_target_evidence_wide "
            "WHERE target_id = ANY(%s)",
            (target_ids,),
        )
        target_evidence = {row["target_id"]: dict(row) for row in cur.fetchall()}
        cur.execute(NELSON_SQL)
        nelson = {
            (row["target_id"], row["indication_id"]): row["nelson_tier"]
            for row in cur.fetchall()
        }
    conn.close()
    for row in rows:
        if row["target_id"] not in target_evidence:
            raise ValueError(f"target evidence missing for {row['target_id']}")
        row.update(target_evidence[row["target_id"]])
        row["nelson_tier"] = nelson.get(
            (row["target_id"], row["indication_id"])
        )

    tiers = encode_nelson_tiers(rows)
    tier_counts = {
        tier: sum(row["nelson_tier"] == tier for row in rows)
        for tier in TIER_VALUE
    }
    tier_counts["missing"] = sum(row["nelson_tier"] is None for row in rows)
    X_base = np.stack([_ml.row_to_feature_vector(row) for row in rows])
    X_nelson = np.column_stack([X_base, tiers])
    X_base = _stack.log_transform_features(X_base, _ml.FEATURE_NAMES)
    X_nelson = _stack.log_transform_features(X_nelson, NELSON_FEATURE_NAMES)
    y = np.asarray([1 if row["y_strict"] else 0 for row in rows], dtype=np.int64)
    groups = np.asarray([row["target_id"] for row in rows], dtype=np.int64)
    print(
        f"Phase 1+ strict cohort: n={len(rows)}, approved={int(y.sum())}, "
        f"targets={len(np.unique(groups))}, tiers={tier_counts}"
    )

    base_ctors = [_stack.make_logreg_l2, _ml.make_lgb_robust, _ml.make_rf]
    nelson_ctors = [
        _stack.make_logreg_l2,
        lambda: _ml.make_lgb_robust(NELSON_FEATURE_NAMES),
        _ml.make_rf,
    ]
    print("Running paired stacked held-out-target evaluations ...", flush=True)
    stacked_base = _final.group_stacked_cv(X_base, y, groups, base_ctors)
    stacked_nelson = _final.group_stacked_cv(X_nelson, y, groups, nelson_ctors)
    report_pair("Stacked", y, groups, stacked_base, stacked_nelson)
    _ml.eval_and_store(
        "stacked_final_with_nelson_target_bootstrap_v5", stacked_nelson, y,
        "ti_phase1plus_strict_consensus_target_with_nelson_impc_dr24",
        "Complete-case Nelson sensitivity: non-placebo programs with one "
        "uniquely best-supported primary-target consensus; complete T0-T3 "
        "adjudication; ordered feature; GroupKFold(target_id); current-day evidence; "
        "unknown non-Nelson measurements fold-locally imputed; audited IMPC "
        "DR24 update with tested-negative zeros only",
        groups=groups,
    )

    print("Running paired LogReg held-out-target evaluations ...", flush=True)
    logreg_base = grouped_logreg_oof(X_base, y, groups)
    logreg_nelson = grouped_logreg_oof(X_nelson, y, groups)
    report_pair("LogReg", y, groups, logreg_base, logreg_nelson)
    _ml.eval_and_store(
        "logreg_final_with_nelson_target_bootstrap_v5", logreg_nelson, y,
        "ti_phase1plus_strict_consensus_target_with_nelson_impc_dr24",
        "Complete-case Nelson sensitivity: LogReg L2 with ordered T0-T3; "
        "uniquely best-supported primary-target consensus; GroupKFold(target_id), "
        "Phase 1+ strict; audited IMPC DR24 update with tested-negative zeros only",
        groups=groups,
    )


if __name__ == "__main__":
    main()
