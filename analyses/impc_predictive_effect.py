"""Record the predictive effect of the audited IMPC DR24 correction."""

import csv
import os
from collections import Counter
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


ROOT = Path(__file__).resolve().parents[1]
FEATURE_LEDGER = ROOT / "data" / "impc_dr24_feature_changes.csv"
OUTPUT = ROOT / "data" / "impc_dr24_predictive_effect.csv"

PAIRS = [
    (
        "stacked_no_nelson",
        "stacked_final_no_nelson_consensus_v3",
        "stacked_final_no_nelson_impc_dr24_v4",
    ),
    (
        "logreg_no_nelson",
        "logreg_final_no_nelson_consensus_v3",
        "logreg_final_no_nelson_impc_dr24_v4",
    ),
    (
        "stacked_with_nelson",
        "stacked_final_with_nelson_consensus_v3",
        "stacked_final_with_nelson_impc_dr24_v4",
    ),
    (
        "logreg_with_nelson",
        "logreg_final_with_nelson_consensus_v3",
        "logreg_final_with_nelson_impc_dr24_v4",
    ),
]


def main():
    with FEATURE_LEDGER.open() as handle:
        transitions = Counter(row["change"] for row in csv.DictReader(handle))

    names = [name for _, old, new in PAIRS for name in (old, new)]
    query = """
        SELECT DISTINCT ON (scoring_function)
          scoring_function, created_at, n_ti_pairs, n_approved, auc_roc,
          auc_roc_ci_lo, auc_roc_ci_hi, brier_score, recall_at_10pct,
          precision_at_10pct, rs_top_decile, calibration_ece
        FROM preclin.benchmark_run
        WHERE scoring_function = ANY(%s)
        ORDER BY scoring_function, created_at DESC
    """
    with psycopg2.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (names,))
            runs = {row["scoring_function"]: dict(row) for row in cur.fetchall()}
    missing = set(names) - set(runs)
    if missing:
        raise RuntimeError(f"Missing benchmark runs: {sorted(missing)}")

    fields = [
        "comparison", "old_run", "new_run", "n_pairs", "n_approved",
        "old_auc", "new_auc", "delta_auc", "old_auc_ci_lo", "old_auc_ci_hi",
        "new_auc_ci_lo", "new_auc_ci_hi", "old_brier", "new_brier",
        "old_recall_at_10pct", "new_recall_at_10pct",
        "old_precision_at_10pct", "new_precision_at_10pct",
        "old_rs_top_decile", "new_rs_top_decile", "old_ece", "new_ece",
        "synthetic_zero_to_unknown", "synthetic_zero_to_observed",
        "unknown_to_observed", "unchanged", "remains_unknown",
    ]
    with OUTPUT.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for label, old_name, new_name in PAIRS:
            old, new = runs[old_name], runs[new_name]
            writer.writerow({
                "comparison": label,
                "old_run": old_name,
                "new_run": new_name,
                "n_pairs": new["n_ti_pairs"],
                "n_approved": new["n_approved"],
                "old_auc": old["auc_roc"],
                "new_auc": new["auc_roc"],
                "delta_auc": new["auc_roc"] - old["auc_roc"],
                "old_auc_ci_lo": old["auc_roc_ci_lo"],
                "old_auc_ci_hi": old["auc_roc_ci_hi"],
                "new_auc_ci_lo": new["auc_roc_ci_lo"],
                "new_auc_ci_hi": new["auc_roc_ci_hi"],
                "old_brier": old["brier_score"],
                "new_brier": new["brier_score"],
                "old_recall_at_10pct": old["recall_at_10pct"],
                "new_recall_at_10pct": new["recall_at_10pct"],
                "old_precision_at_10pct": old["precision_at_10pct"],
                "new_precision_at_10pct": new["precision_at_10pct"],
                "old_rs_top_decile": old["rs_top_decile"],
                "new_rs_top_decile": new["rs_top_decile"],
                "old_ece": old["calibration_ece"],
                "new_ece": new["calibration_ece"],
                **{key: transitions[key] for key in (
                    "synthetic_zero_to_unknown", "synthetic_zero_to_observed",
                    "unknown_to_observed", "unchanged", "remains_unknown",
                )},
            })
    print(f"Wrote {len(PAIRS)} comparisons to {OUTPUT}")


if __name__ == "__main__":
    main()
