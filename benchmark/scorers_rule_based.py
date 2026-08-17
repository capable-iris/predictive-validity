"""Target-scoring function registry.

Each scorer implements the same interface:

    scorer(evidence: dict, context: dict) -> dict {
        'predicted_p_approval': float in [0, 1],
        'predicted_tier': 'low' | 'medium' | 'high',
        'top_supporting_dims': [str],
        'top_concerning_dims': [str],
        'score_confidence': 'low' | 'medium' | 'high',
        'n_features_used': int,
    }

External models (Insilico, Pheiron, someone's ML model) can plug in by:
    1. Implementing the same interface as a Python callable
    2. Registering via register_scorer(name, callable)
    3. Or writing rows to preclin.benchmark_prediction directly with a new
       scoring_function name
"""

import math
import random as rnd
from typing import Callable, Dict, List, Optional, Tuple

# Global registry
SCORERS: Dict[str, Tuple[Callable, str]] = {}


def register_scorer(name: str, fn: Callable, version: str = "v1"):
    SCORERS[name] = (fn, version)


# ============================================================
# Helpers
# ============================================================

def _tier_from_p(p: float) -> str:
    if p >= 0.35:
        return "high"
    if p >= 0.18:
        return "medium"
    return "low"


def _confidence_from_features(n_features_used: int) -> str:
    if n_features_used >= 15:
        return "high"
    if n_features_used >= 8:
        return "medium"
    return "low"


def _sigmoid(x: float) -> float:
    if x < -700:
        return 0.0
    if x > 700:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def _v(evidence: dict, category: str, dim: str, default=None):
    return evidence.get(category, {}).get(dim, default)


def _count_features(evidence: dict) -> int:
    n = 0
    for cat_v in evidence.values():
        if isinstance(cat_v, dict):
            n += sum(1 for v in cat_v.values() if v is not None)
    return n


# ============================================================
# Baseline 1: random
# ============================================================

def scorer_random(evidence: dict, context: dict) -> dict:
    """Random uniform baseline."""
    p = rnd.random()
    return {
        "predicted_p_approval": p,
        "predicted_tier": _tier_from_p(p),
        "top_supporting_dims": [],
        "top_concerning_dims": [],
        "score_confidence": "low",
        "n_features_used": 0,
    }


register_scorer("random_v1", scorer_random, "v1")


# ============================================================
# Baseline 2: family precedent alone (industry heuristic)
# ============================================================

def scorer_family_precedent(evidence: dict, context: dict) -> dict:
    """Score based on how many approvals already exist against the target's family.
    Industry heuristic: 'drug what's been drugged.' """
    n_family = _v(evidence, "I_landscape", "family_approved_count", 0) or 0
    n_gene = _v(evidence, "I_landscape", "gene_approved_count", 0) or 0

    # Log-scale mapping: 0 → 0.10, 5 → 0.25, 20 → 0.40, 100 → 0.60
    p = 0.10 + min(0.50, math.log1p(max(0, n_family)) * 0.075) + \
        min(0.20, math.log1p(max(0, n_gene)) * 0.05)
    p = min(0.85, max(0.05, p))

    supporting = []
    concerning = []
    if n_family >= 2:
        supporting.append(f"family_approved_count={int(n_family)}")
    if n_gene >= 1:
        supporting.append(f"gene_approved_count={int(n_gene)}")
    if n_family == 0 and n_gene == 0:
        concerning.append("no family or gene precedent")

    return {
        "predicted_p_approval": p,
        "predicted_tier": _tier_from_p(p),
        "top_supporting_dims": supporting,
        "top_concerning_dims": concerning,
        "score_confidence": "high" if (n_family + n_gene) > 0 else "low",
        "n_features_used": 2,
    }


register_scorer("family_precedent_v1", scorer_family_precedent, "v1")


# ============================================================
# Baseline 3: genetic-only (Nelson-style)
# ============================================================

def scorer_genetic_only(evidence: dict, context: dict) -> dict:
    """Score based on genetics alone: ClinGen, Mendelian, GWAS, OT genetic."""
    features = 0
    score = 0.0
    supporting, concerning = [], []

    nelson = _v(evidence, "A_genetics", "nelson_tier")
    if nelson:
        features += 1
        if nelson == "T4":
            score += 1.0
            supporting.append("Nelson T4")
        elif nelson == "T3":
            score += 0.7
            supporting.append("Nelson T3")
        elif nelson == "T2":
            score += 0.5
            supporting.append("Nelson T2")
        elif nelson == "T1":
            score += 0.3
            supporting.append("Nelson T1")
        elif nelson == "T0":
            score -= 0.2
            concerning.append("Nelson T0 (no human genetic support)")

    clingen = _v(evidence, "A_genetics", "clingen_n_strong")
    if clingen is not None:
        features += 1
        if clingen >= 1:
            score += 0.6
            supporting.append(f"ClinGen strong/def n={int(clingen)}")

    mendelian = _v(evidence, "A_genetics", "mendelian_n")
    if mendelian is not None:
        features += 1
        if mendelian >= 5:
            score += 0.5
            supporting.append(f"Mendelian n={int(mendelian)}")
        elif mendelian >= 1:
            score += 0.2

    ot_genetic = _v(evidence, "A_genetics", "ot_genetic_max")
    if ot_genetic is not None:
        features += 1
        if ot_genetic >= 0.5:
            score += 0.5
            supporting.append(f"OT genetic {ot_genetic:.2f}")
        elif ot_genetic >= 0.3:
            score += 0.3

    ot_somatic = _v(evidence, "A_genetics", "ot_somatic_score_max")
    if ot_somatic is not None and ot_somatic >= 0.3:
        features += 1
        score += 0.3
        supporting.append(f"OT somatic {ot_somatic:.2f}")

    # Map score → probability. Base rate ~20% for phase-2+ T-I approval.
    p = _sigmoid(score * 0.6 - 1.4)  # calibrated so score=0 → ~0.20

    if not supporting and not concerning:
        concerning.append("no genetic data available")

    return {
        "predicted_p_approval": p,
        "predicted_tier": _tier_from_p(p),
        "top_supporting_dims": supporting[:5],
        "top_concerning_dims": concerning[:5],
        "score_confidence": _confidence_from_features(features),
        "n_features_used": features,
    }


register_scorer("genetic_only_v1", scorer_genetic_only, "v1")


# ============================================================
# Baseline 4: Nelson tier only (single feature)
# ============================================================

def scorer_nelson_only(evidence: dict, context: dict) -> dict:
    """Reproduces Nelson 2015 / Minikel 2024 methodology directly."""
    nelson = _v(evidence, "A_genetics", "nelson_tier")
    if nelson == "T4":
        p, tier_txt = 0.45, "T4 (Mendelian direction-matched)"
    elif nelson == "T3":
        p, tier_txt = 0.35, "T3 (Mendelian match)"
    elif nelson == "T2":
        p, tier_txt = 0.28, "T2 (GWAS coding)"
    elif nelson == "T1":
        p, tier_txt = 0.22, "T1 (GWAS non-coding)"
    elif nelson == "T0":
        p, tier_txt = 0.15, "T0 (no genetic support)"
    else:
        p, tier_txt = 0.18, "unknown"

    return {
        "predicted_p_approval": p,
        "predicted_tier": _tier_from_p(p),
        "top_supporting_dims": [tier_txt] if nelson and nelson != "T0" else [],
        "top_concerning_dims": [tier_txt] if nelson == "T0" else [],
        "score_confidence": "high" if nelson else "low",
        "n_features_used": 1 if nelson else 0,
    }


register_scorer("nelson_only_v1", scorer_nelson_only, "v1")


# ============================================================
# Baseline 5: RS-composite (our best full-model)
# ============================================================

# Weights derived from observed RS in preclin.v_relative_success_clean.
# Positive RS dims get positive weight (log-RS); negative RS dims get negative.
RS_WEIGHTS = {
    # (category, dimension, threshold, direction, weight)
    ("A_genetics", "clingen_n_strong", 1, "gte", 0.55),           # RS 1.73
    ("A_genetics", "mendelian_n", 5, "gte", 0.42),                # RS 1.53
    ("A_genetics", "ot_genetic_max", 0.3, "gte", 0.29),           # RS 1.34
    ("A_genetics", "ot_somatic_score_max", 0.3, "gte", 0.49),     # RS 1.63
    ("A_genetics", "n_hpo_phenotypes", 10, "gte", -0.33),         # corrected RS 0.72 (pleiotropy risk)
    ("B_mechanistic", "tractability_sm", True, "eq", 0.12),
    ("B_mechanistic", "tractability_ab", True, "eq", 0.14),
    ("B_mechanistic", "n_reactome_pathways", 5, "gte", 1.03),     # RS 2.81 (strongest)
    ("B_mechanistic", "n_go_biological_process", 20, "gte", 0.24),
    ("B_mechanistic", "tau_specificity", 0.75, "gte", 0.16),
    ("C_cell", "line_c_lit", 2, "gte", 0.50),                     # RS 1.65
    ("C_cell", "depmap_pan_essential", True, "eq", -2.12),        # RS 0.12 — very negative
    ("D_animal", "line_d_lit", 2, "gte", 0.39),                   # RS 1.47
    ("D_animal", "ot_animal_model_max", 0.3, "gte", 0.25),        # RS 1.28
    ("D_animal", "impc_n_phenotypes", 3, "gte", 0.30),
    ("E_pd", "line_e_lit", 2, "gte", 0.78),                       # RS 2.18 (top signal)
    ("H_safety", "gnomad_pli", 0.9, "gte", -0.25),                # RS 0.78
    ("H_safety", "gnomad_loeuf", 0.35, "lt", -0.30),              # RS 0.74
    ("I_landscape", "n_causal_diseases", 3, "gte", 0.22),
    ("I_landscape", "n_dgidb_drugs", 5, "gte", 0.20),
    ("I_landscape", "family_approved_count", 2, "gte", 0.35),
}

# Human-readable labels
_DIM_LABELS = {
    "clingen_n_strong": "ClinGen Strong/Definitive",
    "mendelian_n": "Mendelian associations",
    "ot_genetic_max": "OT genetic score",
    "ot_somatic_score_max": "OT somatic (cancer)",
    "tractability_sm": "Small-molecule tractable",
    "tractability_ab": "Antibody tractable",
    "n_reactome_pathways": "Reactome pathway membership",
    "n_go_biological_process": "GO-BP annotation depth",
    "tau_specificity": "Tissue-specific (Tau)",
    "line_c_lit": "Cell-pathway literature",
    "depmap_pan_essential": "DepMap pan-essential",
    "line_d_lit": "Animal in vivo literature",
    "ot_animal_model_max": "OT animal model score",
    "impc_n_phenotypes": "IMPC KO phenotypes",
    "n_hpo_phenotypes": "HPO phenotype pleiotropy",
    "line_e_lit": "Human PD engagement literature",
    "gnomad_pli": "gnomAD LoF-intolerant (pLI)",
    "gnomad_loeuf": "gnomAD constrained (LOEUF)",
    "n_causal_diseases": "Causal disease pleiotropy",
    "n_dgidb_drugs": "DGIdb drug precedent",
    "family_approved_count": "Family precedent (approvals)",
}


def scorer_rs_composite(evidence: dict, context: dict) -> dict:
    """Weighted sum of z-scored evidence, weights ∝ log(RS) from historical data."""
    score = 0.0
    features_used = 0
    supporting = []  # list of (weight, dim_label)
    concerning = []

    for cat, dim, thr, direction, w in RS_WEIGHTS:
        v = _v(evidence, cat, dim)
        if v is None:
            continue
        features_used += 1
        passes = False
        try:
            if direction == "gte":
                passes = float(v) >= float(thr)
            elif direction == "lt":
                passes = float(v) < float(thr)
            elif direction == "eq":
                passes = bool(v) == bool(thr)
        except (TypeError, ValueError):
            continue

        if passes:
            score += w
            label = _DIM_LABELS.get(dim, dim)
            if w > 0.15:
                supporting.append((abs(w), label))
            elif w < -0.15:
                concerning.append((abs(w), label))

    # Sigmoid with base rate calibration
    # Empirically: score ~1.5 → 0.35 (top decile), score ~-1 → 0.10
    p = _sigmoid(score - 1.0)

    supporting.sort(reverse=True)
    concerning.sort(reverse=True)

    return {
        "predicted_p_approval": p,
        "predicted_tier": _tier_from_p(p),
        "top_supporting_dims": [s[1] for s in supporting[:5]],
        "top_concerning_dims": [c[1] for c in concerning[:5]],
        "score_confidence": _confidence_from_features(features_used),
        "n_features_used": features_used,
    }


register_scorer("rs_composite_v1", scorer_rs_composite, "v1")


# ============================================================
# Public interface — dispatch by scorer name
# ============================================================

def score_ti(scorer_name: str, evidence: dict, context: dict) -> dict:
    if scorer_name not in SCORERS:
        raise ValueError(f"Unknown scorer: {scorer_name}. Registered: {list(SCORERS.keys())}")
    fn, version = SCORERS[scorer_name]
    result = fn(evidence, context)
    result["_scorer_version"] = version
    return result


def list_scorers() -> List[str]:
    return list(SCORERS.keys())


if __name__ == "__main__":
    # Quick self-test: score TNIK
    demo = {
        "A_genetics": {"nelson_tier": None, "mendelian_n": 2, "clingen_n_strong": 0,
                       "gwas_n_sig": 93, "ot_genetic_max": 0.28, "ot_somatic_score_max": 0.15,
                       "ot_is_mendelian_any": False, "n_hpo_phenotypes": 7},
        "B_mechanistic": {"tractability_sm": True, "tractability_ab": False,
                          "tau_specificity": 0.778, "sc_tau_specificity": 0.919,
                          "n_ppi_partners": 8, "n_reactome_pathways": 5,
                          "n_go_biological_process": 13},
        "C_cell": {"line_c_lit": None, "depmap_pan_essential": False,
                   "depmap_n_dep_lineages": 0},
        "D_animal": {"line_d_lit": None, "impc_n_phenotypes": 3,
                     "ot_animal_model_max": 0.43},
        "E_pd": {"line_e_lit": None},
        "H_safety": {"gnomad_pli": 1.0, "gnomad_loeuf": 0.338},
        "I_landscape": {"n_causal_diseases": 0, "n_dgidb_drugs": 0,
                        "family_approved_count": 12, "gene_approved_count": 0},
    }
    ctx = {"n_programs": 0, "highest_phase": 0}
    print("Scoring TNIK / IPF (Insilico's target):")
    for name in list_scorers():
        r = score_ti(name, demo, ctx)
        print(f"  {name:24}  p={r['predicted_p_approval']:.3f}  tier={r['predicted_tier']:6}  "
              f"n_features={r['n_features_used']}")
