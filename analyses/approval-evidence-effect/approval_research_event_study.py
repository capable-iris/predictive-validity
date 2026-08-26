"""Estimate research-volume changes around a target's first drug approval.

This is an approval-associated event study, not a claim that approval is an
exogenous intervention.  It implements cohort-time difference-in-differences
contrasts with not-yet-approved controls, following the building-block logic
of Callaway and Sant'Anna (2021), rather than a heterogeneous-treatment TWFE
regression.  Dynamic estimates expose anticipation/pre-trends directly.

Primary outcomes:
* distinct GWAS Catalog studies linked to the target in each publication year
* ClinGen classifications for the target in each classification year

The treatment year comes from the normalized, versioned ChEMBL approval tables:
the earliest molecule ``first_approval`` attached to an approved, direct
mechanism for the mapped single-protein target. Drugs with more than one
equally preferred local primary target are excluded from the clinical universe
rather than assigned arbitrarily. Targets enter a cohort only when a dated
clinical program predates approval.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import numpy as np
import psycopg2
from scipy.stats import chi2


ANALYSIS_DIR = Path(__file__).resolve().parent
ANALYSIS_START_YEAR = 2016
ANALYSIS_END_YEAR = 2025  # exclude partial 2026
COHORT_YEARS = tuple(range(2019, 2023))
EVENT_TIMES = (-3, -2, 0, 1, 2, 3)
BASE_EVENT_TIME = -1
POST_EVENT_TIMES = (1, 2, 3)  # exclude approval year: annual date ambiguity
SOURCE_PRIORITY_SQL = """
CASE dt.source
  WHEN 'fda_approval' THEN 1
  WHEN 'llm_sonnet_verified' THEN 2
  WHEN 'therapy_targets_public' THEN 3
  WHEN 'chembl_bulk' THEN 4
  WHEN 'llm_sonnet' THEN 5
  WHEN 'llm_haiku' THEN 6
  ELSE 99
END
"""


UNIVERSE_SQL = f"""
WITH ranked AS (
  SELECT dt.drug_id, dt.target_id, {SOURCE_PRIORITY_SQL} AS source_priority
  FROM preclin.drug_target dt
  WHERE dt.role = 'primary'
),
best AS (
  SELECT *, MIN(source_priority) OVER (PARTITION BY drug_id) AS best_priority
  FROM ranked
),
resolved_drug AS (
  SELECT drug_id, MIN(target_id) AS target_id
  FROM best
  WHERE source_priority = best_priority
  GROUP BY drug_id
  HAVING COUNT(DISTINCT target_id) = 1
),
clinical_target AS (
  SELECT r.target_id, MIN(p.first_trial_date) AS first_trial_date
  FROM resolved_drug r
  JOIN preclin.program p USING (drug_id)
  JOIN preclin.drug d USING (drug_id)
  JOIN public.targets t ON t.id = r.target_id
  WHERE d.is_placebo IS NOT TRUE
    AND (t.pathogen_type IS NULL OR t.pathogen_type = '')
    AND t.ip_type IS DISTINCT FROM 'Genomic'
  GROUP BY r.target_id
)
SELECT c.target_id, t.symbol, c.first_trial_date
FROM clinical_target c
JOIN public.targets t ON t.id = c.target_id
WHERE c.first_trial_date IS NOT NULL
ORDER BY c.target_id
"""


MAPPING_AUDIT_SQL = f"""
WITH ranked AS (
  SELECT dt.drug_id, dt.target_id, {SOURCE_PRIORITY_SQL} AS source_priority
  FROM preclin.drug_target dt
  WHERE dt.role = 'primary'
),
best AS (
  SELECT *, MIN(source_priority) OVER (PARTITION BY drug_id) AS best_priority
  FROM ranked
),
resolution AS (
  SELECT drug_id, COUNT(DISTINCT target_id) AS n_best_targets
  FROM best
  WHERE source_priority = best_priority
  GROUP BY drug_id
)
SELECT COUNT(*) AS mapped_drugs,
       COUNT(*) FILTER (WHERE n_best_targets = 1) AS resolved_drugs,
       COUNT(*) FILTER (WHERE n_best_targets > 1) AS ambiguous_drugs
FROM resolution
"""


GWAS_SQL = """
SELECT ga.target_id,
       EXTRACT(YEAR FROM gsd.evidence_available_date)::integer AS evidence_year,
       COUNT(DISTINCT ga.study_accession)::integer AS n_records
FROM public.gwas_associations ga
JOIN preclin.gwas_study_date gsd USING (study_accession)
WHERE gsd.evidence_available_date >= DATE '2016-01-01'
  AND gsd.evidence_available_date < DATE '2026-01-01'
GROUP BY ga.target_id, EXTRACT(YEAR FROM gsd.evidence_available_date)::integer
"""


CLINGEN_SQL = """
SELECT target_id,
       LEFT(classified_date, 4)::integer AS evidence_year,
       COUNT(*)::integer AS n_records
FROM public.clingen_validity
WHERE classified_date ~ '^[0-9]{4}'
  AND LEFT(classified_date, 4)::integer BETWEEN 2016 AND 2025
GROUP BY target_id, LEFT(classified_date, 4)::integer
"""


CHEMBL_APPROVAL_SQL = """
SELECT target_id, first_approval_year, chembl_db_version, release_date,
       source, source_url, mapping_policy, imported_at,
       supporting_mechanism_count
FROM preclin.v_chembl_target_first_approval
ORDER BY target_id
"""


GWAS_DATE_AUDIT_SQL = """
SELECT COUNT(DISTINCT ga.study_accession) AS association_studies,
       COUNT(DISTINCT gsd.study_accession) AS dated_studies
FROM public.gwas_associations ga
LEFT JOIN preclin.gwas_study_date gsd USING (study_accession)
"""


@dataclass(frozen=True)
class DynamicEstimate:
    event_time: int
    treated_targets: int
    mean_controls: float
    raw_att: float
    log1p_att: float
    observed_mean: float
    counterfactual_mean: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--bootstrap", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--csv-out",
        type=Path,
        default=ANALYSIS_DIR / "data/approval_research_event_study.csv",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=ANALYSIS_DIR / "data/approval_research_event_study_summary.json",
    )
    return parser.parse_args()


def direct_database_url(url: str) -> str:
    parsed = urlsplit(url)
    if "-pooler." not in (parsed.hostname or ""):
        return url
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc.replace("-pooler.", ".", 1),
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


def load_panel(conn):
    with conn.cursor() as cur:
        cur.execute(MAPPING_AUDIT_SQL)
        mapped_drugs, resolved_drugs, ambiguous_drugs = cur.fetchone()

        cur.execute(GWAS_DATE_AUDIT_SQL)
        gwas_studies, dated_gwas_studies = cur.fetchone()
        if gwas_studies != dated_gwas_studies:
            raise RuntimeError(
                f"GWAS date coverage incomplete: {dated_gwas_studies}/{gwas_studies}"
            )

        cur.execute(UNIVERSE_SQL)
        universe_all = cur.fetchall()
        cur.execute(CHEMBL_APPROVAL_SQL)
        approval_rows = cur.fetchall()
        if not approval_rows:
            raise RuntimeError(
                "No ChEMBL target approval history is loaded; run migration 25 "
                "and fetch_chembl_target_approval_history.py"
            )
        approval_by_target = {
            int(row[0]): row[1]
            for row in approval_rows
        }
        approval_metadata = approval_rows[0][2:8]
        if any(row[2:8] != approval_metadata for row in approval_rows):
            raise RuntimeError("ChEMBL approval view returned mixed release metadata")
        (
            approval_version,
            approval_release_date,
            approval_source,
            approval_source_url,
            approval_mapping_policy,
            approval_imported_at,
        ) = approval_metadata
        # A target absent from ChEMBL target mapping has unknown rather than
        # never-approved status and cannot serve as a valid control.
        universe = [row for row in universe_all if row[0] in approval_by_target]

        target_ids = np.asarray([row[0] for row in universe], dtype=np.int64)
        symbols = [row[1] for row in universe]
        first_trial_year = np.asarray(
            [row[2].year for row in universe], dtype=np.int64
        )
        first_approval_year = np.asarray(
            [
                approval_by_target[row[0]]
                if approval_by_target[row[0]] is not None
                else 9999
                for row in universe
            ],
            dtype=np.int64,
        )
        target_index = {target_id: i for i, target_id in enumerate(target_ids)}
        years = np.arange(ANALYSIS_START_YEAR, ANALYSIS_END_YEAR + 1)
        year_index = {year: i for i, year in enumerate(years)}

        outcomes = {
            "gwas_studies": np.zeros((len(universe), len(years)), dtype=np.float64),
            "clingen_classifications": np.zeros(
                (len(universe), len(years)), dtype=np.float64
            ),
        }
        for name, sql in (("gwas_studies", GWAS_SQL), ("clingen_classifications", CLINGEN_SQL)):
            cur.execute(sql)
            for target_id, evidence_year, n_records in cur.fetchall():
                row_index = target_index.get(target_id)
                column_index = year_index.get(evidence_year)
                if row_index is not None and column_index is not None:
                    outcomes[name][row_index, column_index] = n_records

    audit = {
        "mapped_primary_drugs": int(mapped_drugs),
        "unambiguously_resolved_drugs": int(resolved_drugs),
        "ambiguous_drugs_excluded": int(ambiguous_drugs),
        "dated_clinical_targets_before_chembl_mapping": int(len(universe_all)),
        "dated_clinical_targets_with_chembl_mapping": int(len(universe)),
        "targets_ever_approved": int(np.sum(first_approval_year < 9999)),
        "gwas_studies_with_associations": int(gwas_studies),
        "gwas_studies_with_dates": int(dated_gwas_studies),
        "analysis_start_year": ANALYSIS_START_YEAR,
        "analysis_end_year": ANALYSIS_END_YEAR,
        "approval_history_source": approval_source,
        "approval_history_source_url": approval_source_url,
        "approval_history_database_view": "preclin.v_chembl_target_first_approval",
        "approval_history_chembl_version": approval_version,
        "approval_history_release_date": approval_release_date.isoformat(),
        "approval_history_mapping_policy": approval_mapping_policy,
        "approval_history_distinct_supporting_mechanisms": int(
            sum(row[8] for row in approval_rows)
        ),
    }
    return (
        target_ids,
        symbols,
        first_trial_year,
        first_approval_year,
        years,
        outcomes,
        audit,
    )


def estimate_dynamic(
    outcome: np.ndarray,
    years: np.ndarray,
    first_trial_year: np.ndarray,
    first_approval_year: np.ndarray,
    control_group: str,
) -> dict[int, DynamicEstimate]:
    year_index = {int(year): i for i, year in enumerate(years)}
    cohort_results: dict[int, list[tuple[int, float, float, float, float, int]]] = {
        event_time: [] for event_time in EVENT_TIMES
    }

    for cohort_year in COHORT_YEARS:
        treated = (first_approval_year == cohort_year) & (
            first_trial_year <= cohort_year - 1
        )
        n_treated = int(np.sum(treated))
        if n_treated == 0:
            continue
        base_column = year_index[cohort_year + BASE_EVENT_TIME]

        for event_time in EVENT_TIMES:
            calendar_year = cohort_year + event_time
            if calendar_year not in year_index:
                continue
            latest_untreated_year = max(calendar_year, cohort_year - 1)
            eligible = first_trial_year <= cohort_year - 1
            if control_group == "not_yet_approved":
                controls = eligible & (first_approval_year > latest_untreated_year)
            elif control_group == "never_approved":
                controls = eligible & (first_approval_year == 9999)
            else:
                raise ValueError(f"unknown control group: {control_group}")
            controls &= ~treated
            n_controls = int(np.sum(controls))
            if n_controls == 0:
                continue

            event_column = year_index[calendar_year]
            treated_event = outcome[treated, event_column]
            treated_base = outcome[treated, base_column]
            control_event = outcome[controls, event_column]
            control_base = outcome[controls, base_column]

            raw_att = float(
                np.mean(treated_event - treated_base)
                - np.mean(control_event - control_base)
            )
            log_att = float(
                np.mean(np.log1p(treated_event) - np.log1p(treated_base))
                - np.mean(np.log1p(control_event) - np.log1p(control_base))
            )
            counterfactual = float(
                np.mean(treated_base) + np.mean(control_event - control_base)
            )
            cohort_results[event_time].append(
                (
                    n_treated,
                    raw_att,
                    log_att,
                    float(np.mean(treated_event)),
                    counterfactual,
                    n_controls,
                )
            )

    dynamic: dict[int, DynamicEstimate] = {}
    for event_time, estimates in cohort_results.items():
        if not estimates:
            continue
        weights = np.asarray([item[0] for item in estimates], dtype=np.float64)
        weights /= weights.sum()
        dynamic[event_time] = DynamicEstimate(
            event_time=event_time,
            treated_targets=int(sum(item[0] for item in estimates)),
            mean_controls=float(np.average([item[5] for item in estimates], weights=weights)),
            raw_att=float(np.average([item[1] for item in estimates], weights=weights)),
            log1p_att=float(np.average([item[2] for item in estimates], weights=weights)),
            observed_mean=float(np.average([item[3] for item in estimates], weights=weights)),
            counterfactual_mean=float(
                np.average([item[4] for item in estimates], weights=weights)
            ),
        )
    return dynamic


def summarize_dynamic(dynamic: dict[int, DynamicEstimate]) -> dict[str, float]:
    post = [dynamic[event_time] for event_time in POST_EVENT_TIMES]
    cumulative_observed = float(sum(item.observed_mean for item in post))
    cumulative_counterfactual = float(sum(item.counterfactual_mean for item in post))
    cumulative_excess = float(sum(item.raw_att for item in post))
    return {
        "approval_year_raw_att": dynamic[0].raw_att,
        "mean_annual_raw_att_years_1_to_3": float(
            np.mean([item.raw_att for item in post])
        ),
        "cumulative_excess_records_years_1_to_3": cumulative_excess,
        "cumulative_observed_records_years_1_to_3": cumulative_observed,
        "cumulative_counterfactual_records_years_1_to_3": cumulative_counterfactual,
        "mean_log1p_att_years_1_to_3": float(
            np.mean([item.log1p_att for item in post])
        ),
    }


def leave_one_treated_out(
    outcome: np.ndarray,
    years: np.ndarray,
    symbols: list[str],
    first_trial_year: np.ndarray,
    first_approval_year: np.ndarray,
    control_group: str,
) -> dict[str, object]:
    """Report the range after removing each treated target in turn."""
    treated = np.isin(first_approval_year, COHORT_YEARS) & (
        first_trial_year <= first_approval_year - 1
    )
    estimates: list[tuple[float, str]] = []
    for index in np.flatnonzero(treated):
        keep = np.ones(len(first_approval_year), dtype=bool)
        keep[index] = False
        dynamic = estimate_dynamic(
            outcome[keep],
            years,
            first_trial_year[keep],
            first_approval_year[keep],
            control_group,
        )
        estimate = summarize_dynamic(dynamic)[
            "cumulative_excess_records_years_1_to_3"
        ]
        estimates.append((estimate, symbols[index]))
    minimum = min(estimates)
    maximum = max(estimates)
    return {
        "minimum_cumulative_excess": minimum[0],
        "minimum_when_removed": minimum[1],
        "maximum_cumulative_excess": maximum[0],
        "maximum_when_removed": maximum[1],
    }


def bootstrap_estimates(
    outcome: np.ndarray,
    years: np.ndarray,
    first_trial_year: np.ndarray,
    first_approval_year: np.ndarray,
    control_group: str,
    iterations: int,
    seed: int,
):
    rng = np.random.default_rng(seed)
    n_targets = len(first_trial_year)
    dynamic_samples = {event_time: [] for event_time in EVENT_TIMES}
    summary_samples: dict[str, list[float]] = {}

    for _ in range(iterations):
        sample = rng.integers(0, n_targets, size=n_targets)
        dynamic = estimate_dynamic(
            outcome[sample],
            years,
            first_trial_year[sample],
            first_approval_year[sample],
            control_group,
        )
        if any(event_time not in dynamic for event_time in EVENT_TIMES):
            continue
        for event_time in EVENT_TIMES:
            dynamic_samples[event_time].append(
                (dynamic[event_time].raw_att, dynamic[event_time].log1p_att)
            )
        summary = summarize_dynamic(dynamic)
        for key, value in summary.items():
            summary_samples.setdefault(key, []).append(value)

    return dynamic_samples, summary_samples


def percentile_interval(values) -> tuple[float, float]:
    array = np.asarray(values, dtype=np.float64)
    array = array[np.isfinite(array)]
    if len(array) == 0:
        return float("nan"), float("nan")
    low, high = np.quantile(array, [0.025, 0.975])
    return float(low), float(high)


def pretrend_test(
    dynamic: dict[int, DynamicEstimate], dynamic_samples
) -> tuple[float, float]:
    pre_times = (-3, -2)
    estimate = np.asarray([dynamic[event_time].raw_att for event_time in pre_times])
    samples = np.asarray(
        [
            [dynamic_samples[event_time][i][0] for event_time in pre_times]
            for i in range(min(len(dynamic_samples[event_time]) for event_time in pre_times))
        ],
        dtype=np.float64,
    )
    covariance = np.cov(samples, rowvar=False)
    statistic = float(estimate @ np.linalg.pinv(covariance) @ estimate)
    return statistic, float(chi2.sf(statistic, df=len(pre_times)))


def write_outputs(results, audit, args):
    args.csv_out.parent.mkdir(parents=True, exist_ok=True)
    with args.csv_out.open("w", newline="") as stream:
        fieldnames = [
            "outcome",
            "control_group",
            "event_time",
            "treated_targets",
            "mean_controls",
            "raw_att",
            "raw_ci_lo",
            "raw_ci_hi",
            "log1p_att",
            "log1p_ci_lo",
            "log1p_ci_hi",
            "approx_percent_change",
            "approx_percent_ci_lo",
            "approx_percent_ci_hi",
            "observed_mean",
            "counterfactual_mean",
        ]
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for result in results:
            for event_time, estimate in result["dynamic"].items():
                raw_samples = [v[0] for v in result["dynamic_samples"][event_time]]
                log_samples = [v[1] for v in result["dynamic_samples"][event_time]]
                raw_ci = percentile_interval(raw_samples)
                log_ci = percentile_interval(log_samples)
                writer.writerow(
                    {
                        "outcome": result["outcome"],
                        "control_group": result["control_group"],
                        "event_time": event_time,
                        "treated_targets": estimate.treated_targets,
                        "mean_controls": f"{estimate.mean_controls:.6f}",
                        "raw_att": f"{estimate.raw_att:.9f}",
                        "raw_ci_lo": f"{raw_ci[0]:.9f}",
                        "raw_ci_hi": f"{raw_ci[1]:.9f}",
                        "log1p_att": f"{estimate.log1p_att:.9f}",
                        "log1p_ci_lo": f"{log_ci[0]:.9f}",
                        "log1p_ci_hi": f"{log_ci[1]:.9f}",
                        "approx_percent_change": f"{100 * np.expm1(estimate.log1p_att):.6f}",
                        "approx_percent_ci_lo": f"{100 * np.expm1(log_ci[0]):.6f}",
                        "approx_percent_ci_hi": f"{100 * np.expm1(log_ci[1]):.6f}",
                        "observed_mean": f"{estimate.observed_mean:.9f}",
                        "counterfactual_mean": f"{estimate.counterfactual_mean:.9f}",
                    }
                )

    serializable_results = []
    for result in results:
        summary_intervals = {
            key: percentile_interval(values)
            for key, values in result["summary_samples"].items()
        }
        serializable_results.append(
            {
                "outcome": result["outcome"],
                "control_group": result["control_group"],
                "dynamic": {
                    str(event_time): asdict(estimate)
                    for event_time, estimate in result["dynamic"].items()
                },
                "summary": result["summary"],
                "summary_95pct_intervals": summary_intervals,
                "pretrend_wald_chi2": result["pretrend_statistic"],
                "pretrend_wald_p": result["pretrend_p"],
                "leave_one_treated_target_out": result[
                    "leave_one_treated_target_out"
                ],
                "treated_target_coverage_years_1_to_3": result[
                    "treated_target_coverage_years_1_to_3"
                ],
            }
        )

    payload = {
        "analysis": "approval-associated target research event study",
        "estimand": (
            "cohort-weighted difference-in-differences change relative to event "
            "year -1; target-cluster percentile bootstrap"
        ),
        "causal_warning": (
            "Approval is not exogenous. Estimates are approval-associated changes; "
            "pre-trends and research begun before publication limit causal interpretation."
        ),
        "cohort_years": list(COHORT_YEARS),
        "event_times": list(EVENT_TIMES),
        "reference_event_time": BASE_EVENT_TIME,
        "post_summary_event_times": list(POST_EVENT_TIMES),
        "bootstrap_iterations": args.bootstrap,
        "random_seed": args.seed,
        "audit": audit,
        "results": serializable_results,
    }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main() -> None:
    args = parse_args()
    database_url = direct_database_url(os.environ["DATABASE_URL"])
    conn = psycopg2.connect(
        database_url,
        connect_timeout=15,
        options="-c statement_timeout=900000",
    )
    try:
        (
            target_ids,
            symbols,
            first_trial_year,
            first_approval_year,
            years,
            outcomes,
            audit,
        ) = load_panel(conn)
    finally:
        conn.close()

    del target_ids
    print(json.dumps(audit, indent=2, sort_keys=True))
    results = []
    for outcome_index, (outcome_name, outcome) in enumerate(outcomes.items()):
        for control_index, control_group in enumerate(
            ("not_yet_approved", "never_approved")
        ):
            dynamic = estimate_dynamic(
                outcome,
                years,
                first_trial_year,
                first_approval_year,
                control_group,
            )
            dynamic_samples, summary_samples = bootstrap_estimates(
                outcome,
                years,
                first_trial_year,
                first_approval_year,
                control_group,
                args.bootstrap,
                args.seed + 100 * outcome_index + control_index,
            )
            summary = summarize_dynamic(dynamic)
            pretrend_statistic, pretrend_p = pretrend_test(
                dynamic, dynamic_samples
            )
            result = {
                "outcome": outcome_name,
                "control_group": control_group,
                "dynamic": dynamic,
                "dynamic_samples": dynamic_samples,
                "summary": summary,
                "summary_samples": summary_samples,
                "pretrend_statistic": pretrend_statistic,
                "pretrend_p": pretrend_p,
                "leave_one_treated_target_out": leave_one_treated_out(
                    outcome,
                    years,
                    symbols,
                    first_trial_year,
                    first_approval_year,
                    control_group,
                ),
                "treated_target_coverage_years_1_to_3": {
                    "targets": int(
                        np.sum(
                            np.isin(first_approval_year, COHORT_YEARS)
                            & (first_trial_year <= first_approval_year - 1)
                        )
                    ),
                    "targets_with_at_least_one_record": int(
                        sum(
                            np.sum(
                                outcome[
                                    index,
                                    [
                                        int(np.flatnonzero(years == first_approval_year[index] + k)[0])
                                        for k in POST_EVENT_TIMES
                                    ],
                                ]
                            )
                            > 0
                            for index in np.flatnonzero(
                                np.isin(first_approval_year, COHORT_YEARS)
                                & (first_trial_year <= first_approval_year - 1)
                            )
                        )
                    ),
                },
            }
            results.append(result)
            post_ci = percentile_interval(
                summary_samples["cumulative_excess_records_years_1_to_3"]
            )
            print(
                f"{outcome_name} / {control_group}: "
                f"3-year excess={summary['cumulative_excess_records_years_1_to_3']:.3f} "
                f"[{post_ci[0]:.3f}, {post_ci[1]:.3f}], "
                f"pretrend p={pretrend_p:.4f}"
            )

    write_outputs(results, audit, args)
    print(f"wrote {args.csv_out}")
    print(f"wrote {args.json_out}")


if __name__ == "__main__":
    main()
