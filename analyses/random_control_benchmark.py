"""Matched random-ranking and prevalence controls for the headline cohort."""

import argparse
import csv
import os
from pathlib import Path

import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor
from sklearn.metrics import roc_auc_score

try:
    from analyses.phase1_cohort import PHASE1_SQL
except ModuleNotFoundError:  # Direct `python analyses/random_control_benchmark.py`.
    from phase1_cohort import PHASE1_SQL


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "random_control_target_bootstrap_v5.csv"
OBSERVED_RUNS = (
    "stacked_final_no_nelson_target_bootstrap_v5",
    "logreg_final_no_nelson_target_bootstrap_v5",
    "stacked_final_with_nelson_target_bootstrap_v5",
    "logreg_final_with_nelson_target_bootstrap_v5",
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def quantiles(values):
    return np.quantile(values, [0.025, 0.5, 0.975]).tolist()


def random_target_rank_distribution(y, groups, iterations, seed):
    """Assign one random rank per target, with random within-target tie breaks."""
    rng = np.random.default_rng(seed)
    unique_groups, inverse = np.unique(groups, return_inverse=True)
    n = len(y)
    k = max(1, int(round(0.10 * n)))
    positives = y.sum()
    aucs = np.empty(iterations)
    recalls = np.empty(iterations)
    precisions = np.empty(iterations)
    relative_success = np.empty(iterations)
    for iteration in range(iterations):
        target_scores = rng.random(len(unique_groups))
        scores = target_scores[inverse] + rng.random(n) * 1e-12
        aucs[iteration] = roc_auc_score(y, scores)
        top = np.argpartition(scores, -k)[-k:]
        top_positives = y[top].sum()
        recalls[iteration] = top_positives / positives
        precisions[iteration] = top_positives / k
        p_top = top_positives / k
        p_rest = (positives - top_positives) / (n - k)
        relative_success[iteration] = p_top / p_rest if p_rest else np.nan
    return {
        "auc": quantiles(aucs),
        "recall_at_10pct": quantiles(recalls),
        "precision_at_10pct": quantiles(precisions),
        "rs_top_decile": quantiles(relative_success[~np.isnan(relative_success)]),
        "auc_samples": aucs,
    }


def main():
    args = parse_args()
    if args.iterations < 100:
        raise ValueError("Use at least 100 random-control iterations")
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(PHASE1_SQL)
        rows = cur.fetchall()
        cur.execute(
            """
            SELECT DISTINCT ON (scoring_function)
              scoring_function, auc_roc
            FROM preclin.benchmark_run
            WHERE scoring_function = ANY(%s)
            ORDER BY scoring_function, created_at DESC
            """,
            (list(OBSERVED_RUNS),),
        )
        observed = {row["scoring_function"]: float(row["auc_roc"])
                    for row in cur.fetchall()}
    missing = set(OBSERVED_RUNS) - set(observed)
    if missing:
        conn.close()
        raise RuntimeError(f"Run v5 benchmarks first: {sorted(missing)}")

    y = np.asarray([1 if row["y_strict"] else 0 for row in rows], dtype=np.int64)
    groups = np.asarray([row["target_id"] for row in rows], dtype=np.int64)
    null = random_target_rank_distribution(y, groups, args.iterations, args.seed)
    prevalence = float(y.mean())
    prevalence_brier = prevalence * (1.0 - prevalence)

    fields = [
        "control", "iterations", "seed", "n_pairs", "n_approved", "n_targets",
        "auc_lo", "auc_median", "auc_hi", "recall10_lo", "recall10_median",
        "recall10_hi", "precision10_lo", "precision10_median", "precision10_hi",
        "rs10_lo", "rs10_median", "rs10_hi", "observed_run", "observed_auc",
        "empirical_p_random_ge_observed",
    ]
    with OUTPUT.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for run_name in OBSERVED_RUNS:
            observed_auc = observed[run_name]
            empirical_p = (
                1 + int(np.sum(null["auc_samples"] >= observed_auc))
            ) / (args.iterations + 1)
            writer.writerow({
                "control": "random_target_rank",
                "iterations": args.iterations,
                "seed": args.seed,
                "n_pairs": len(y),
                "n_approved": int(y.sum()),
                "n_targets": len(np.unique(groups)),
                "auc_lo": null["auc"][0],
                "auc_median": null["auc"][1],
                "auc_hi": null["auc"][2],
                "recall10_lo": null["recall_at_10pct"][0],
                "recall10_median": null["recall_at_10pct"][1],
                "recall10_hi": null["recall_at_10pct"][2],
                "precision10_lo": null["precision_at_10pct"][0],
                "precision10_median": null["precision_at_10pct"][1],
                "precision10_hi": null["precision_at_10pct"][2],
                "rs10_lo": null["rs_top_decile"][0],
                "rs10_median": null["rs_top_decile"][1],
                "rs10_hi": null["rs_top_decile"][2],
                "observed_run": run_name,
                "observed_auc": observed_auc,
                "empirical_p_random_ge_observed": empirical_p,
            })

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO preclin.benchmark_run
              (scoring_function, scoring_version, cohort_definition,
               n_ti_pairs, n_approved, n_failed, auc_roc, auc_roc_ci_lo,
               auc_roc_ci_hi, recall_at_10pct, precision_at_10pct,
               rs_top_decile, notes)
            VALUES (%s, 'v5', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                "random_target_rank_control_v5",
                "ti_phase1plus_strict_consensus_target_impc_dr24",
                len(y), int(y.sum()), int(len(y) - y.sum()),
                null["auc"][1], null["auc"][0], null["auc"][2],
                null["recall_at_10pct"][1], null["precision_at_10pct"][1],
                null["rs_top_decile"][1],
                f"Random target-level ranks; {args.iterations} iterations; "
                f"seed={args.seed}; interval is random-null distribution",
            ),
        )
        cur.execute(
            """
            INSERT INTO preclin.benchmark_run
              (scoring_function, scoring_version, cohort_definition,
               n_ti_pairs, n_approved, n_failed, auc_roc, brier_score,
               calibration_ece, notes)
            VALUES (%s, 'v5', %s, %s, %s, %s, 0.5, %s, 0.0, %s)
            """,
            (
                "prevalence_only_control_v5",
                "ti_phase1plus_strict_consensus_target_impc_dr24",
                len(y), int(y.sum()), int(len(y) - y.sum()), prevalence_brier,
                f"Constant cohort prevalence={prevalence:.12f}; calibration null",
            ),
        )
    conn.commit()
    conn.close()
    print(
        f"Random target-rank AUC median={null['auc'][1]:.4f} "
        f"[{null['auc'][0]:.4f}, {null['auc'][2]:.4f}]"
    )
    for run_name in OBSERVED_RUNS:
        p_value = (1 + int(np.sum(null["auc_samples"] >= observed[run_name]))) / (
            args.iterations + 1
        )
        print(f"  {run_name}: observed={observed[run_name]:.4f}, p={p_value:.6f}")
    print(f"Prevalence-only Brier={prevalence_brier:.6f}")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
