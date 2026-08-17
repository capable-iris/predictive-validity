"""Run benchmark: pull T-I cohort, apply scorer, compute metrics, store to DB.

Usage:
    python3 11_benchmark_runner.py                 # runs all scorers
    python3 11_benchmark_runner.py rs_composite_v1 # runs one scorer
"""

import json
import math
import os
import random
import sys
from typing import List, Tuple

import psycopg2
from psycopg2.extras import execute_values, Json, RealDictCursor

# Local imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib import import_module
_scorers = import_module("scorers_rule_based")

random.seed(42)

DB_URL = os.environ["DATABASE_URL"]


# ============================================================
# Cohort + evidence loading
# ============================================================

COHORT_SQL = """
    SELECT
      ti.target_id, ti.indication_id,
      ti.any_approved, ti.any_approved_us,
      ti.any_efficacy_fail, ti.any_safety_fail,
      ti.max_phase_reached, ti.n_programs, ti.n_drugs, ti.n_sponsors,
      ti.outcomes_broad_all,
      t.symbol AS target_symbol,
      i.display_name AS indication_name,
      i.therapeutic_area,
      -- Target-level evidence (wide)
      tw.line_b_lit, tw.line_c_lit, tw.line_d_lit, tw.line_e_lit,
      tw.impc_n_phenotypes, tw.family_approved_count, tw.gene_approved_count,
      tw.tau_specificity, tw.max_tissue_tpm, tw.max_tissue_name,
      tw.n_high_tissues, tw.n_ppi_partners, tw.n_reactome_pathways,
      tw.n_dgidb_drugs, tw.n_causal_diseases, tw.n_suggestive_diseases,
      tw.n_hpo_phenotypes, tw.ot_l2g_score_max, tw.ot_somatic_score_max,
      tw.ot_rna_expression_max, tw.mendelian_n_dominant, tw.mendelian_n_recessive,
      tw.ot_is_mendelian_any, tw.sc_tau_specificity, tw.sc_max_cell_value,
      tw.sc_max_cell_type, tw.sc_n_cell_types_expressed,
      tw.n_go_biological_process, tw.n_go_molecular_function, tw.n_go_cellular_component,
      -- Target-level gene-native
      tw.gnomad_pli, tw.gnomad_loeuf, tw.depmap_pan_essential,
      tw.depmap_n_dep_lineages, tw.depmap_mean_effect,
      tw.tractability_sm, tw.tractability_ab, tw.tractability_protac,
      tw.clingen_n_strong, tw.mendelian_n, tw.gwas_n_sig,
      tw.ot_overall_max, tw.ot_genetic_max, tw.ot_animal_model_max, tw.ot_known_drug_max,
      -- Nelson tier per (target, indication)
      (SELECT value_text FROM preclin.evidence_score
        WHERE subject_type='target_indication' AND subject_id = ti.target_id
          AND subject_id2 = ti.indication_id AND dimension = 'nelson_tier'
        ORDER BY extracted_at DESC LIMIT 1) AS nelson_tier
    FROM preclin.v_target_indication_program ti
    JOIN public.targets t ON t.id = ti.target_id
    JOIN preclin.indication i ON i.indication_id = ti.indication_id
    JOIN preclin.v_target_evidence_wide tw ON tw.target_id = ti.target_id
    WHERE ti.max_phase_reached >= %(min_phase)s
      -- Filter placebos + microbial + still-in-dev
      AND NOT EXISTS (
        SELECT 1 FROM preclin.program p
        JOIN preclin.drug d ON d.drug_id = p.drug_id
        WHERE p.indication_id = ti.indication_id AND d.is_placebo IS TRUE
      )
      AND (t.pathogen_type IS NULL OR t.pathogen_type = '')
      -- Must have some meaningful outcome (not just in-dev)
      AND ti.outcomes_broad_all NOT SIMILAR TO 'in_dev%%'
"""


def load_cohort(conn, min_phase: int = 2):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(COHORT_SQL, {"min_phase": min_phase})
        return cur.fetchall()


def row_to_evidence_context(row: dict) -> Tuple[dict, dict, bool]:
    """Convert a cohort row to (evidence_dict, context_dict, approved_label)."""
    evidence = {
        "A_genetics": {
            "nelson_tier": row.get("nelson_tier"),
            "mendelian_n": row.get("mendelian_n"),
            "mendelian_n_dominant": row.get("mendelian_n_dominant"),
            "mendelian_n_recessive": row.get("mendelian_n_recessive"),
            "clingen_n_strong": row.get("clingen_n_strong"),
            "gwas_n_sig": row.get("gwas_n_sig"),
            "ot_genetic_max": row.get("ot_genetic_max"),
            "ot_l2g_score_max": row.get("ot_l2g_score_max"),
            "ot_somatic_score_max": row.get("ot_somatic_score_max"),
            "ot_rna_expression_max": row.get("ot_rna_expression_max"),
            "ot_is_mendelian_any": row.get("ot_is_mendelian_any"),
            "n_hpo_phenotypes": row.get("n_hpo_phenotypes"),
        },
        "B_mechanistic": {
            "line_b_lit": row.get("line_b_lit"),
            "tractability_sm": row.get("tractability_sm"),
            "tractability_ab": row.get("tractability_ab"),
            "tractability_protac": row.get("tractability_protac"),
            "tau_specificity": row.get("tau_specificity"),
            "sc_tau_specificity": row.get("sc_tau_specificity"),
            "n_ppi_partners": row.get("n_ppi_partners"),
            "n_reactome_pathways": row.get("n_reactome_pathways"),
            "n_go_biological_process": row.get("n_go_biological_process"),
            "n_go_molecular_function": row.get("n_go_molecular_function"),
            "n_go_cellular_component": row.get("n_go_cellular_component"),
        },
        "C_cell": {
            "line_c_lit": row.get("line_c_lit"),
            "depmap_pan_essential": row.get("depmap_pan_essential"),
            "depmap_n_dep_lineages": row.get("depmap_n_dep_lineages"),
            "depmap_mean_effect": row.get("depmap_mean_effect"),
        },
        "D_animal": {
            "line_d_lit": row.get("line_d_lit"),
            "ot_animal_model_max": row.get("ot_animal_model_max"),
            "impc_n_phenotypes": row.get("impc_n_phenotypes"),
        },
        "E_pd": {
            "line_e_lit": row.get("line_e_lit"),
        },
        "H_safety": {
            "gnomad_pli": row.get("gnomad_pli"),
            "gnomad_loeuf": row.get("gnomad_loeuf"),
        },
        "I_landscape": {
            "family_approved_count": row.get("family_approved_count"),
            "gene_approved_count": row.get("gene_approved_count"),
            "n_causal_diseases": row.get("n_causal_diseases"),
            "n_suggestive_diseases": row.get("n_suggestive_diseases"),
            "n_dgidb_drugs": row.get("n_dgidb_drugs"),
        },
    }
    context = {
        "target_symbol": row.get("target_symbol"),
        "indication_name": row.get("indication_name"),
        "therapeutic_area": row.get("therapeutic_area"),
        "n_programs": row.get("n_programs"),
        "n_drugs": row.get("n_drugs"),
        "n_sponsors": row.get("n_sponsors"),
        "highest_phase": row.get("max_phase_reached"),
    }
    y = bool(row.get("any_approved"))
    return evidence, context, y


# ============================================================
# Metrics
# ============================================================

def auc_roc(y: List[bool], p: List[float]) -> float:
    """Wilcoxon-Mann-Whitney U → AUC."""
    pos = [pp for yy, pp in zip(y, p) if yy]
    neg = [pp for yy, pp in zip(y, p) if not yy]
    if not pos or not neg:
        return None
    n_wins = 0
    n_ties = 0
    for a in pos:
        for b in neg:
            if a > b:
                n_wins += 1
            elif a == b:
                n_ties += 1
    return (n_wins + 0.5 * n_ties) / (len(pos) * len(neg))


def brier_score(y: List[bool], p: List[float]) -> float:
    return sum((float(yy) - pp) ** 2 for yy, pp in zip(y, p)) / len(y)


def recall_at_top_k(y: List[bool], p: List[float], k_pct: float) -> float:
    n = len(y)
    if n == 0:
        return 0.0
    k = max(1, int(round(k_pct * n)))
    ranked = sorted(zip(p, y), reverse=True)
    top_k_positives = sum(1 for _, yy in ranked[:k] if yy)
    total_positives = sum(1 for yy in y if yy)
    if total_positives == 0:
        return 0.0
    return top_k_positives / total_positives


def precision_at_top_k(y: List[bool], p: List[float], k_pct: float) -> float:
    n = len(y)
    if n == 0:
        return 0.0
    k = max(1, int(round(k_pct * n)))
    ranked = sorted(zip(p, y), reverse=True)
    return sum(1 for _, yy in ranked[:k] if yy) / k


def rs_by_top_decile(y: List[bool], p: List[float]) -> float:
    """RS = P(approved | in top decile) / P(approved | rest)."""
    n = len(y)
    k = max(1, int(round(0.1 * n)))
    ranked = sorted(zip(p, y), reverse=True)
    top_y = [yy for _, yy in ranked[:k]]
    rest_y = [yy for _, yy in ranked[k:]]
    if not top_y or not rest_y:
        return None
    p_top = sum(top_y) / len(top_y)
    p_rest = sum(rest_y) / len(rest_y)
    if p_rest == 0:
        return None
    return p_top / p_rest


def rs_by_top_quartile(y: List[bool], p: List[float]) -> float:
    n = len(y)
    k = max(1, int(round(0.25 * n)))
    ranked = sorted(zip(p, y), reverse=True)
    top_y = [yy for _, yy in ranked[:k]]
    rest_y = [yy for _, yy in ranked[k:]]
    if not top_y or not rest_y:
        return None
    p_top = sum(top_y) / len(top_y)
    p_rest = sum(rest_y) / len(rest_y)
    if p_rest == 0:
        return None
    return p_top / p_rest


def calibration_ece(y: List[bool], p: List[float], n_bins: int = 10) -> float:
    """Expected Calibration Error."""
    if not y:
        return 0.0
    bin_edges = [i / n_bins for i in range(n_bins + 1)]
    ece = 0.0
    for lo, hi in zip(bin_edges[:-1], bin_edges[1:]):
        in_bin = [(yy, pp) for yy, pp in zip(y, p) if lo <= pp < hi or (hi == 1.0 and pp == 1.0)]
        if not in_bin:
            continue
        avg_p = sum(pp for _, pp in in_bin) / len(in_bin)
        avg_y = sum(int(yy) for yy, _ in in_bin) / len(in_bin)
        ece += (len(in_bin) / len(y)) * abs(avg_p - avg_y)
    return ece


def bootstrap_metric(y: List[bool], p: List[float], metric_fn, n_iter: int = 200) -> Tuple[float, float, float]:
    if not y:
        return None, None, None
    point = metric_fn(y, p)
    if point is None:
        return None, None, None
    n = len(y)
    idx = list(range(n))
    samples = []
    for _ in range(n_iter):
        s = [random.randrange(n) for _ in range(n)]
        sy = [y[i] for i in s]
        sp = [p[i] for i in s]
        try:
            v = metric_fn(sy, sp)
            if v is not None:
                samples.append(v)
        except Exception:
            pass
    if not samples:
        return point, None, None
    samples.sort()
    lo = samples[int(0.025 * len(samples))]
    hi = samples[int(0.975 * len(samples))]
    return point, lo, hi


# ============================================================
# Runner
# ============================================================

def run_benchmark(scorer_name: str, cohort_name: str = "ti_phase2plus",
                  min_phase: int = 2, cutoff_date=None):
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False

    print(f"\n== Benchmark: {scorer_name} on {cohort_name} ==")
    cohort = load_cohort(conn, min_phase=min_phase)
    print(f"  cohort size: {len(cohort)}")

    scorer_fn = _scorers.SCORERS[scorer_name][0]
    scorer_version = _scorers.SCORERS[scorer_name][1]

    predictions = []
    for row in cohort:
        evidence, context, y = row_to_evidence_context(row)
        result = scorer_fn(evidence, context)
        predictions.append({
            "target_id": row["target_id"],
            "indication_id": row["indication_id"],
            "evidence": evidence,
            "context": context,
            "y_approved": y,
            "y_highest_phase": row.get("max_phase_reached"),
            "y_n_programs": row.get("n_programs"),
            "predicted_p_approval": result["predicted_p_approval"],
            "predicted_tier": result["predicted_tier"],
            "top_supporting_dims": result["top_supporting_dims"],
            "top_concerning_dims": result["top_concerning_dims"],
            "score_confidence": result["score_confidence"],
            "n_features_used": result["n_features_used"],
        })

    # Rank
    predictions.sort(key=lambda r: r["predicted_p_approval"], reverse=True)
    for i, p in enumerate(predictions):
        p["predicted_rank"] = i + 1

    # Compute metrics
    y_list = [p["y_approved"] for p in predictions]
    p_list = [p["predicted_p_approval"] for p in predictions]
    n_approved = sum(y_list)
    n_failed = len(y_list) - n_approved
    print(f"  n_approved={n_approved}, n_failed={n_failed}")

    auc, auc_lo, auc_hi = bootstrap_metric(y_list, p_list, auc_roc)
    brier, brier_lo, brier_hi = bootstrap_metric(y_list, p_list, brier_score)
    r5 = recall_at_top_k(y_list, p_list, 0.05)
    r10 = recall_at_top_k(y_list, p_list, 0.10)
    r20 = recall_at_top_k(y_list, p_list, 0.20)
    p10 = precision_at_top_k(y_list, p_list, 0.10)
    rs10 = rs_by_top_decile(y_list, p_list)
    rs25 = rs_by_top_quartile(y_list, p_list)
    ece = calibration_ece(y_list, p_list)

    print(f"  AUC={auc:.3f} [{auc_lo:.3f}, {auc_hi:.3f}]"
          if auc is not None else "  AUC=n/a")
    print(f"  Brier={brier:.3f}, recall@10%={r10:.3f}, precision@10%={p10:.3f}")
    print(f"  RS(top 10%)={rs10:.2f}"
          if rs10 is not None else "  RS(top 10%)=n/a")
    print(f"  ECE={ece:.3f}")

    # Store to DB
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO preclin.benchmark_run
              (scoring_function, scoring_version, cutoff_date, realization_years,
               cohort_definition, n_ti_pairs, n_approved, n_failed, n_excluded_indev,
               auc_roc, auc_roc_ci_lo, auc_roc_ci_hi,
               brier_score, brier_score_ci_lo, brier_score_ci_hi,
               recall_at_5pct, recall_at_10pct, recall_at_20pct,
               precision_at_10pct, rs_top_decile, rs_top_quartile, calibration_ece,
               notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING benchmark_run_id
        """, (scorer_name, scorer_version, cutoff_date, None,
              cohort_name, len(cohort), n_approved, n_failed, 0,
              auc, auc_lo, auc_hi,
              brier, brier_lo, brier_hi,
              r5, r10, r20, p10, rs10, rs25, ece,
              f"scorer={scorer_name} cohort={cohort_name}"))
        run_id = cur.fetchone()[0]

        pred_rows = [(
            run_id, p["target_id"], p["indication_id"],
            float(p["predicted_p_approval"]), p["predicted_tier"], p["predicted_rank"],
            p["top_supporting_dims"], p["top_concerning_dims"],
            p["score_confidence"], p["n_features_used"],
            p["y_approved"], None, p["y_highest_phase"], p["y_n_programs"],
            Json(p["evidence"])
        ) for p in predictions]

        execute_values(cur, """
            INSERT INTO preclin.benchmark_prediction
              (benchmark_run_id, target_id, indication_id,
               predicted_p_approval, predicted_tier, predicted_rank,
               top_supporting_dims, top_concerning_dims,
               score_confidence, n_features_used,
               y_approved, y_realization_date, y_highest_phase, y_n_programs,
               evidence_snapshot)
            VALUES %s
        """, pred_rows, page_size=1000)
        conn.commit()
    conn.close()
    print(f"  Stored as benchmark_run_id={run_id}")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    scorers_to_run = ([which] if which != "all" else _scorers.list_scorers())
    for s in scorers_to_run:
        run_benchmark(s)
