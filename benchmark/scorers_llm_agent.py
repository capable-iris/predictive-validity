"""LLM agent scorer via Anthropic SDK (not subprocess).

Reads evidence dossier per T-I, returns predicted P(approval) + rationale.
Parallelizable — uses concurrent.futures for throughput.

Cost estimate: ~$0.02/T-I × 400 sampled T-Is = ~$8, ~15 min.
"""

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

import numpy as np
import psycopg2
from anthropic import Anthropic
from psycopg2.extras import execute_values, Json, RealDictCursor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib import import_module
_runner = import_module("runner")
_robust = import_module("scorers_ml")

def _load_api_key():
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key:
        return env_key
    # Iris .env has working key
    for path in [
        "/Users/melissadu/Documents/projects/capable/production/iris/.env",
        "/Users/melissadu/Documents/projects/capable/.env",
    ]:
        try:
            with open(path) as f:
                for line in f:
                    if line.startswith("ANTHROPIC_API_KEY="):
                        return line.split("=", 1)[1].strip()
        except FileNotFoundError:
            continue
    raise RuntimeError("No ANTHROPIC_API_KEY found")


CLIENT = Anthropic(api_key=_load_api_key())

DB_URL = os.environ["DATABASE_URL"]


def compact_evidence(row: dict) -> str:
    lines = [
        f"Target: **{row.get('target_symbol') or 'unknown'}**  "
        f"Indication: **{row.get('indication_name') or 'unknown'}**  "
        f"TA: {row.get('therapeutic_area') or 'unknown'}",
    ]
    ev_keys = [
        ("Genetics", [
            ("Nelson tier", "nelson_tier"),
            ("ClinGen strong", "clingen_n_strong"),
            ("Mendelian associations", "mendelian_n"),
            ("Mendelian dominant", "mendelian_n_dominant"),
            ("Mendelian recessive", "mendelian_n_recessive"),
            ("GWAS significant", "gwas_n_sig"),
            ("OT genetic score", "ot_genetic_max"),
            ("OT somatic score", "ot_somatic_score_max"),
            ("HPO phenotype breadth", "n_hpo_phenotypes"),
        ]),
        ("Mechanistic", [
            ("Tractable small mol", "tractability_sm"),
            ("Tractable antibody", "tractability_ab"),
            ("Bulk Tau specificity", "tau_specificity"),
            ("Single-cell Tau", "sc_tau_specificity"),
            ("Max cell type", "sc_max_cell_type"),
            ("Reactome pathways", "n_reactome_pathways"),
            ("PPI partners", "n_ppi_partners"),
        ]),
        ("Cell (CRISPR)", [
            ("DepMap pan-essential", "depmap_pan_essential"),
            ("DepMap dep lineages", "depmap_n_dep_lineages"),
            ("DepMap mean effect", "depmap_mean_effect"),
            ("Line C lit", "line_c_lit"),
        ]),
        ("Animal", [
            ("Line D lit", "line_d_lit"),
            ("OT animal model", "ot_animal_model_max"),
            ("IMPC KO phenotypes", "impc_n_phenotypes"),
        ]),
        ("Human PD", [
            ("Line E lit", "line_e_lit"),
        ]),
        ("Safety", [
            ("gnomAD pLI", "gnomad_pli"),
            ("gnomAD LOEUF", "gnomad_loeuf"),
        ]),
        ("Landscape", [
            ("Prior approvals (family)", "family_approved_count"),
            ("Prior approvals (gene)", "gene_approved_count"),
            ("Causal diseases", "n_causal_diseases"),
            ("DGIdb drugs", "n_dgidb_drugs"),
        ]),
    ]
    for section, keys in ev_keys:
        vals = []
        for label, key in keys:
            v = row.get(key)
            if v is None or v == "":
                continue
            if isinstance(v, float) and v != v:  # NaN
                continue
            vals.append(f"{label}={v}")
        if vals:
            lines.append(f"- **{section}**: " + ", ".join(vals))
    return "\n".join(lines)


PROMPT_TEMPLATE = """Predict FDA approval probability for the (target × indication) pair below.

**Base rate** in our cohort of Phase 1+ T-I pairs is 3%. Anchor near this.

**Evidence:**
{evidence_block}

**Notes:**
- STRICT outcome: was this drug approved SPECIFICALLY for this indication? (Not any indication.)
- Weight biology-driven signals (genetics, cell essentiality) most.
- DepMap pan-essential targets are systemically undruggable → very low probability.
- Downweight "family precedent" and "DGIdb drugs" which correlate with approval but are largely landscape proxies.

Return single JSON, no other text:
{{"p_approval": 0.XX, "confidence": "high|medium|low",
  "primary_positive_signal": "...", "primary_concern": "...",
  "rationale": "one sentence"}}"""


def score_one(row: dict) -> dict:
    prompt = PROMPT_TEMPLATE.format(evidence_block=compact_evidence(row))
    try:
        r = CLIENT.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = r.content[0].text if r.content else ""
        m = re.search(r"\{[^{}]*\"p_approval\"[^{}]*\}", text) or re.search(r"\{.*?\}", text, re.DOTALL)
        if m:
            parsed = json.loads(m.group(0))
            p = float(parsed.get("p_approval", 0.03))
            parsed["p_approval"] = max(0.0, min(1.0, p))
            parsed["_cost_input"] = r.usage.input_tokens
            parsed["_cost_output"] = r.usage.output_tokens
            return parsed
    except Exception as e:
        return {"error": str(e)[:100], "p_approval": 0.03}
    return {"error": "no_json", "p_approval": 0.03}


def sample_stratified(rows: List[dict], n: int, seed: int = 42):
    rng = np.random.default_rng(seed)
    pos = [i for i, r in enumerate(rows) if r["y_strict"]]
    neg = [i for i, r in enumerate(rows) if not r["y_strict"]]
    n_pos = min(len(pos), n // 2)
    n_neg = min(len(neg), n - n_pos)
    idx = np.concatenate([
        rng.choice(pos, size=n_pos, replace=False),
        rng.choice(neg, size=n_neg, replace=False),
    ])
    rng.shuffle(idx)
    return idx.tolist()


def main(limit: int = 400):
    conn = psycopg2.connect(DB_URL)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(_robust.STRICT_COHORT_SQL)
        rows = cur.fetchall()
    conn.close()
    print(f"Full strict cohort: {len(rows)}")
    print(f"Sampling {limit} stratified (50/50)")

    idx = sample_stratified(rows, limit)
    sampled = [rows[i] for i in idx]
    print(f"Sampled {len(sampled)}: {sum(1 for r in sampled if r['y_strict'])} approved")

    predictions = []
    total_in = 0
    total_out = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(score_one, r): i for i, r in enumerate(sampled)}
        for i, fut in enumerate(as_completed(futures)):
            row_idx = futures[fut]
            row = sampled[row_idx]
            try:
                result = fut.result()
            except Exception as e:
                result = {"error": str(e)[:100], "p_approval": 0.03}
            p = float(result.get("p_approval", 0.03))
            total_in += result.get("_cost_input", 0)
            total_out += result.get("_cost_output", 0)
            predictions.append({
                "target_id": row["target_id"],
                "indication_id": row["indication_id"],
                "predicted_p_approval": p,
                "rationale": result.get("rationale", "")[:400],
                "primary_positive": result.get("primary_positive_signal", ""),
                "primary_concern": result.get("primary_concern", ""),
                "confidence": result.get("confidence", "low"),
                "y_approved": bool(row["y_strict"]),
            })
            if (i + 1) % 25 == 0:
                est_cost = total_in * 3e-6 + total_out * 15e-6  # Sonnet 4 pricing
                print(f"  [{i+1}/{len(sampled)}] est_cost ${est_cost:.2f}", flush=True)

    y = [p["y_approved"] for p in predictions]
    p_list = [p["predicted_p_approval"] for p in predictions]
    auc, auc_lo, auc_hi = _runner.bootstrap_metric(y, p_list, _runner.auc_roc)
    brier = _runner.brier_score(y, p_list)
    r10 = _runner.recall_at_top_k(y, p_list, 0.10)
    p10 = _runner.precision_at_top_k(y, p_list, 0.10)
    rs10 = _runner.rs_by_top_decile(y, p_list)
    ece = _runner.calibration_ece(y, p_list)
    print(f"\n== Sonnet agent (via SDK) — {len(y)} T-Is ==")
    print(f"AUC = {auc:.3f} [{auc_lo:.3f}, {auc_hi:.3f}]")
    print(f"Brier = {brier:.3f}, R@10% = {r10:.3f}, P@10% = {p10:.3f}, RS10 = {rs10:.2f}, ECE = {ece:.3f}")
    est_cost = total_in * 3e-6 + total_out * 15e-6
    print(f"Total est cost: ${est_cost:.2f}")

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
            RETURNING benchmark_run_id
        """, ("sonnet_agent_sdk_v1", "v1_sdk_parallel",
              f"ti_phase2plus_strict_sampled_{limit}", len(y),
              sum(y), len(y) - sum(y),
              auc, auc_lo, auc_hi, brier, r10, p10, rs10, ece,
              f"Sonnet agent via SDK, parallel 8 workers, est ${est_cost:.2f}"))
        run_id = cur.fetchone()[0]

        pred_rows = []
        ranked = sorted(zip(predictions, p_list), key=lambda t: t[1], reverse=True)
        rank_map = {(p[0]["target_id"], p[0]["indication_id"]): i+1 for i, p in enumerate(ranked)}
        for pred in predictions:
            tier = "high" if pred["predicted_p_approval"] >= 0.35 else \
                   ("medium" if pred["predicted_p_approval"] >= 0.18 else "low")
            pred_rows.append((run_id, pred["target_id"], pred["indication_id"],
                              pred["predicted_p_approval"], tier,
                              rank_map[(pred["target_id"], pred["indication_id"])],
                              [pred["primary_positive"][:200]] if pred["primary_positive"] else None,
                              [pred["primary_concern"][:200]] if pred["primary_concern"] else None,
                              pred["confidence"], 20, pred["y_approved"], None, None, None,
                              Json({"rationale": pred["rationale"]})))
        execute_values(cur, """
            INSERT INTO preclin.benchmark_prediction
              (benchmark_run_id, target_id, indication_id, predicted_p_approval,
               predicted_tier, predicted_rank, top_supporting_dims, top_concerning_dims,
               score_confidence, n_features_used, y_approved, y_realization_date,
               y_highest_phase, y_n_programs, evidence_snapshot)
            VALUES %s
        """, pred_rows, page_size=500)
        conn.commit()
    conn.close()
    print(f"Stored as benchmark_run_id={run_id}")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    main(n)
