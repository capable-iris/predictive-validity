-- T-I unit + Relative Success (RS) metric views
-- Rebuilds Pheiron's analytical primitives on top of our data.

BEGIN;

-- ============================================================
-- v_target_indication_program — one row per (target × indication), rolled up
-- ============================================================
DROP VIEW IF EXISTS preclin.v_target_indication_program CASCADE;
CREATE VIEW preclin.v_target_indication_program AS
WITH ti_programs AS (
  -- For each (target × indication), aggregate all programs that pursued it
  SELECT
    dt.target_id,
    p.indication_id,
    COUNT(DISTINCT p.program_id) AS n_programs,
    COUNT(DISTINCT p.drug_id) AS n_drugs,
    COUNT(DISTINCT p.sponsor_name) AS n_sponsors,
    MAX(p.highest_phase) AS max_phase_reached,
    -- Outcome rollup
    BOOL_OR(po.approved_us OR po.approved_ex_us) AS any_approved,
    BOOL_OR(po.approved_us) AS any_approved_us,
    BOOL_OR(po.outcome = 'efficacy_fail' OR po.outcome_broad = 'presumptive_efficacy_fail_ph3') AS any_efficacy_fail,
    BOOL_OR(po.outcome = 'safety_fail') AS any_safety_fail,
    STRING_AGG(DISTINCT po.outcome, '|' ORDER BY po.outcome) AS outcomes_all,
    STRING_AGG(DISTINCT po.outcome_broad, '|' ORDER BY po.outcome_broad) AS outcomes_broad_all,
    MIN(p.first_trial_date) AS first_trial_date,
    MAX(p.last_trial_date) AS last_trial_date
  FROM preclin.program p
  JOIN preclin.v_drug_target dt ON dt.drug_id = p.drug_id AND dt.role = 'primary'
  JOIN preclin.program_outcome po ON po.program_id = p.program_id
  GROUP BY dt.target_id, p.indication_id
)
SELECT
  target_id, indication_id,
  n_programs, n_drugs, n_sponsors, max_phase_reached,
  any_approved, any_approved_us, any_efficacy_fail, any_safety_fail,
  outcomes_all, outcomes_broad_all,
  first_trial_date, last_trial_date,
  -- Resolved outcome for RS calc
  CASE
    WHEN any_approved THEN 'approved'
    WHEN any_efficacy_fail THEN 'efficacy_fail'
    WHEN any_safety_fail THEN 'safety_fail'
    WHEN max_phase_reached >= 2 THEN 'attempted_no_approval'
    ELSE 'other'
  END AS ti_outcome
FROM ti_programs;

COMMENT ON VIEW preclin.v_target_indication_program IS
'Pheiron-style T-I unit: one row per (target × indication), with rolled-up outcome across all programs that pursued it.';

-- ============================================================
-- v_target_evidence_wide — all evidence at TARGET level (indication-agnostic)
-- ============================================================
DROP VIEW IF EXISTS preclin.v_target_evidence_wide CASCADE;
CREATE VIEW preclin.v_target_evidence_wide AS
SELECT
  t.id AS target_id, t.symbol, t.family, t.tdl,
  t.tractability_sm, t.tractability_ab, t.tractability_protac,
  -- Line scores (from PubMed Haiku)
  MAX(CASE WHEN es.dimension = 'line_b_lit' THEN es.value_numeric END) AS line_b_lit,
  MAX(CASE WHEN es.dimension = 'line_c_lit' THEN es.value_numeric END) AS line_c_lit,
  MAX(CASE WHEN es.dimension = 'line_d_lit' THEN es.value_numeric END) AS line_d_lit,
  MAX(CASE WHEN es.dimension = 'line_e_lit' THEN es.value_numeric END) AS line_e_lit,
  -- Genome-browser derived
  MAX(CASE WHEN es.dimension = 'impc_n_phenotypes' THEN es.value_numeric END) AS impc_n_phenotypes,
  MAX(CASE WHEN es.dimension = 'family_approved_count' THEN es.value_numeric END) AS family_approved_count,
  MAX(CASE WHEN es.dimension = 'gene_approved_count' THEN es.value_numeric END) AS gene_approved_count,
  -- New extras
  MAX(CASE WHEN es.dimension = 'tau_specificity' THEN es.value_numeric END) AS tau_specificity,
  MAX(CASE WHEN es.dimension = 'max_tissue_tpm' THEN es.value_numeric END) AS max_tissue_tpm,
  MAX(CASE WHEN es.dimension = 'max_tissue_name' THEN es.value_text END) AS max_tissue_name,
  MAX(CASE WHEN es.dimension = 'n_high_tissues' THEN es.value_numeric END) AS n_high_tissues,
  MAX(CASE WHEN es.dimension = 'n_ppi_partners' THEN es.value_numeric END) AS n_ppi_partners,
  MAX(CASE WHEN es.dimension = 'n_reactome_pathways' THEN es.value_numeric END) AS n_reactome_pathways,
  MAX(CASE WHEN es.dimension = 'n_dgidb_drugs' THEN es.value_numeric END) AS n_dgidb_drugs,
  MAX(CASE WHEN es.dimension = 'n_causal_diseases' THEN es.value_numeric END) AS n_causal_diseases,
  MAX(CASE WHEN es.dimension = 'n_suggestive_diseases' THEN es.value_numeric END) AS n_suggestive_diseases,
  MAX(CASE WHEN es.dimension = 'n_hpo_phenotypes' THEN es.value_numeric END) AS n_hpo_phenotypes,
  MAX(CASE WHEN es.dimension = 'ot_l2g_score_max' THEN es.value_numeric END) AS ot_l2g_score_max,
  MAX(CASE WHEN es.dimension = 'ot_somatic_score_max' THEN es.value_numeric END) AS ot_somatic_score_max,
  MAX(CASE WHEN es.dimension = 'ot_rna_expression_max' THEN es.value_numeric END) AS ot_rna_expression_max,
  MAX(CASE WHEN es.dimension = 'mendelian_n_dominant' THEN es.value_numeric END) AS mendelian_n_dominant,
  MAX(CASE WHEN es.dimension = 'mendelian_n_recessive' THEN es.value_numeric END) AS mendelian_n_recessive,
  BOOL_OR(CASE WHEN es.dimension = 'ot_is_mendelian_any' THEN es.value_boolean END) AS ot_is_mendelian_any,
  -- Single-cell + GO from 07_ingest_more
  MAX(CASE WHEN es.dimension = 'sc_tau_specificity' THEN es.value_numeric END) AS sc_tau_specificity,
  MAX(CASE WHEN es.dimension = 'sc_max_cell_value' THEN es.value_numeric END) AS sc_max_cell_value,
  MAX(CASE WHEN es.dimension = 'sc_max_cell_type' THEN es.value_text END) AS sc_max_cell_type,
  MAX(CASE WHEN es.dimension = 'sc_n_cell_types_expressed' THEN es.value_numeric END) AS sc_n_cell_types_expressed,
  MAX(CASE WHEN es.dimension = 'n_go_biological_process' THEN es.value_numeric END) AS n_go_biological_process,
  MAX(CASE WHEN es.dimension = 'n_go_molecular_function' THEN es.value_numeric END) AS n_go_molecular_function,
  MAX(CASE WHEN es.dimension = 'n_go_cellular_component' THEN es.value_numeric END) AS n_go_cellular_component,
  -- gnomAD, DepMap, ClinGen, Mendelian, GWAS, OT-composite from public.*
  gc.pli AS gnomad_pli, gc.loeuf AS gnomad_loeuf,
  ges.pan_essential AS depmap_pan_essential,
  ges.n_dependent_lineages AS depmap_n_dep_lineages,
  ges.mean_effect AS depmap_mean_effect,
  gclingen.n_strong AS clingen_n_strong,
  gmend.n AS mendelian_n,
  ggwas.n_sig AS gwas_n_sig,
  gte.ot_overall_max, gte.ot_genetic_max, gte.ot_animal_model_max, gte.ot_known_drug_max
FROM public.targets t
LEFT JOIN preclin.evidence_score es ON es.subject_type = 'target' AND es.subject_id = t.id
LEFT JOIN public.gene_constraint gc ON gc.target_id = t.id
LEFT JOIN public.gene_essentiality_summary ges ON ges.target_id = t.id
LEFT JOIN LATERAL (
  SELECT count(*) FILTER (WHERE classification IN ('Definitive','Strong')) AS n_strong
  FROM public.clingen_validity WHERE target_id = t.id
) gclingen ON TRUE
LEFT JOIN LATERAL (
  SELECT count(*) AS n FROM public.mendelian_associations WHERE target_id = t.id
) gmend ON TRUE
LEFT JOIN LATERAL (
  SELECT count(*) FILTER (WHERE p_value < 5e-8) AS n_sig
  FROM public.gwas_associations WHERE target_id = t.id
) ggwas ON TRUE
LEFT JOIN LATERAL (
  SELECT
    MAX(overall_score) AS ot_overall_max,
    MAX(genetic_score) AS ot_genetic_max,
    MAX(animal_model_score) AS ot_animal_model_max,
    MAX(known_drug_score) AS ot_known_drug_max
  FROM public.target_evidence WHERE target_id = t.id
) gte ON TRUE
WHERE t.ip_type != 'Genomic'
GROUP BY t.id, t.symbol, t.family, t.tdl,
         t.tractability_sm, t.tractability_ab, t.tractability_protac,
         gc.pli, gc.loeuf, ges.pan_essential, ges.n_dependent_lineages, ges.mean_effect,
         gclingen.n_strong, gmend.n, ggwas.n_sig,
         gte.ot_overall_max, gte.ot_genetic_max, gte.ot_animal_model_max, gte.ot_known_drug_max;

COMMENT ON VIEW preclin.v_target_evidence_wide IS
'Every evidence dimension per target, wide-form. Indication-agnostic. Basis for RS calculations.';

-- ============================================================
-- v_relative_success — Pheiron RS metric per evidence dimension
-- ============================================================
DROP VIEW IF EXISTS preclin.v_relative_success CASCADE;
CREATE VIEW preclin.v_relative_success AS
WITH ti_pool AS (
  SELECT ti.*, tw.*
  FROM preclin.v_target_indication_program ti
  JOIN preclin.v_target_evidence_wide tw ON tw.target_id = ti.target_id
  WHERE ti.max_phase_reached >= 2  -- reached efficacy testing
),
tests AS (
  SELECT 'A. Rare Mendelian ≥1 dominant' AS dimension, 'A_genetics' AS category,
         (mendelian_n_dominant >= 1) AS supported, any_approved, ti_outcome FROM ti_pool WHERE mendelian_n_dominant IS NOT NULL
  UNION ALL SELECT 'A. Rare Mendelian ≥1 recessive', 'A_genetics',
         (mendelian_n_recessive >= 1), any_approved, ti_outcome FROM ti_pool WHERE mendelian_n_recessive IS NOT NULL
  UNION ALL SELECT 'A. Mendelian associations ≥5', 'A_genetics',
         (mendelian_n >= 5), any_approved, ti_outcome FROM ti_pool WHERE mendelian_n IS NOT NULL
  UNION ALL SELECT 'A. ClinGen Strong/Definitive ≥1', 'A_genetics',
         (clingen_n_strong >= 1), any_approved, ti_outcome FROM ti_pool WHERE clingen_n_strong IS NOT NULL
  UNION ALL SELECT 'A. GWAS significant ≥50', 'A_genetics',
         (gwas_n_sig >= 50), any_approved, ti_outcome FROM ti_pool WHERE gwas_n_sig IS NOT NULL
  UNION ALL SELECT 'A. OT genetic ≥0.3', 'A_genetics',
         (ot_genetic_max >= 0.3), any_approved, ti_outcome FROM ti_pool WHERE ot_genetic_max IS NOT NULL
  UNION ALL SELECT 'A. OT L2G colocalization ≥0.5', 'A_genetics',
         (ot_l2g_score_max >= 0.5), any_approved, ti_outcome FROM ti_pool WHERE ot_l2g_score_max IS NOT NULL
  UNION ALL SELECT 'A. OT somatic (cancer) ≥0.3', 'A_genetics',
         (ot_somatic_score_max >= 0.3), any_approved, ti_outcome FROM ti_pool WHERE ot_somatic_score_max IS NOT NULL
  UNION ALL SELECT 'A. HPO phenotype breadth ≥10', 'A_genetics',
         (n_hpo_phenotypes >= 10), any_approved, ti_outcome FROM ti_pool WHERE n_hpo_phenotypes IS NOT NULL
  UNION ALL SELECT 'B. Tractable — small mol', 'B_mechanistic',
         (tractability_sm IS TRUE), any_approved, ti_outcome FROM ti_pool WHERE tractability_sm IS NOT NULL
  UNION ALL SELECT 'B. Tractable — antibody', 'B_mechanistic',
         (tractability_ab IS TRUE), any_approved, ti_outcome FROM ti_pool WHERE tractability_ab IS NOT NULL
  UNION ALL SELECT 'B. Tissue-specific (Tau ≥0.75)', 'B_mechanistic',
         (tau_specificity >= 0.75), any_approved, ti_outcome FROM ti_pool WHERE tau_specificity IS NOT NULL
  UNION ALL SELECT 'B. High-expression max_tpm ≥100', 'B_mechanistic',
         (max_tissue_tpm >= 100), any_approved, ti_outcome FROM ti_pool WHERE max_tissue_tpm IS NOT NULL
  UNION ALL SELECT 'B. Reactome pathways ≥5', 'B_mechanistic',
         (n_reactome_pathways >= 5), any_approved, ti_outcome FROM ti_pool WHERE n_reactome_pathways IS NOT NULL
  UNION ALL SELECT 'B. Hub-like (PPI partners ≥50)', 'B_mechanistic',
         (n_ppi_partners >= 50), any_approved, ti_outcome FROM ti_pool WHERE n_ppi_partners IS NOT NULL
  UNION ALL SELECT 'C. Line C lit high (≥2)', 'C_cell',
         (line_c_lit >= 2), any_approved, ti_outcome FROM ti_pool WHERE line_c_lit IS NOT NULL
  UNION ALL SELECT 'C. DepMap pan-essential', 'C_cell',
         (depmap_pan_essential IS TRUE), any_approved, ti_outcome FROM ti_pool WHERE depmap_pan_essential IS NOT NULL
  UNION ALL SELECT 'D. Line D lit high (≥2)', 'D_animal',
         (line_d_lit >= 2), any_approved, ti_outcome FROM ti_pool WHERE line_d_lit IS NOT NULL
  UNION ALL SELECT 'D. OT animal model ≥0.3', 'D_animal',
         (ot_animal_model_max >= 0.3), any_approved, ti_outcome FROM ti_pool WHERE ot_animal_model_max IS NOT NULL
  UNION ALL SELECT 'D. IMPC ≥3 phenotypes', 'D_animal',
         (impc_n_phenotypes >= 3), any_approved, ti_outcome FROM ti_pool WHERE impc_n_phenotypes IS NOT NULL
  UNION ALL SELECT 'E. Line E lit high (≥2)', 'E_pd',
         (line_e_lit >= 2), any_approved, ti_outcome FROM ti_pool WHERE line_e_lit IS NOT NULL
  UNION ALL SELECT 'H. gnomAD pLI ≥0.9', 'H_safety',
         (gnomad_pli >= 0.9), any_approved, ti_outcome FROM ti_pool WHERE gnomad_pli IS NOT NULL
  UNION ALL SELECT 'H. gnomAD LOEUF <0.35', 'H_safety',
         (gnomad_loeuf < 0.35), any_approved, ti_outcome FROM ti_pool WHERE gnomad_loeuf IS NOT NULL
  UNION ALL SELECT 'I. Prior approvals (family ≥2)', 'I_landscape',
         (family_approved_count >= 2), any_approved, ti_outcome FROM ti_pool WHERE family_approved_count IS NOT NULL
  UNION ALL SELECT 'I. DGIdb drug precedent ≥5', 'I_landscape',
         (n_dgidb_drugs >= 5), any_approved, ti_outcome FROM ti_pool WHERE n_dgidb_drugs IS NOT NULL
  UNION ALL SELECT 'I. Causal disease pleiotropy ≥3', 'I_landscape',
         (n_causal_diseases >= 3), any_approved, ti_outcome FROM ti_pool WHERE n_causal_diseases IS NOT NULL
)
SELECT
  category, dimension,
  COUNT(*) FILTER (WHERE supported) AS n_supported,
  COUNT(*) FILTER (WHERE NOT supported) AS n_not_supported,
  COUNT(*) FILTER (WHERE supported AND any_approved) AS n_supported_approved,
  COUNT(*) FILTER (WHERE NOT supported AND any_approved) AS n_not_supported_approved,
  ROUND(100.0 * COUNT(*) FILTER (WHERE supported AND any_approved) / NULLIF(COUNT(*) FILTER (WHERE supported), 0), 1) AS supported_pct_approved,
  ROUND(100.0 * COUNT(*) FILTER (WHERE NOT supported AND any_approved) / NULLIF(COUNT(*) FILTER (WHERE NOT supported), 0), 1) AS not_supported_pct_approved,
  -- Relative Success = P(approved | supported) / P(approved | not supported)
  ROUND(
    ((COUNT(*) FILTER (WHERE supported AND any_approved)::numeric / NULLIF(COUNT(*) FILTER (WHERE supported), 0))
    /
    NULLIF((COUNT(*) FILTER (WHERE NOT supported AND any_approved)::numeric / NULLIF(COUNT(*) FILTER (WHERE NOT supported), 0)), 0))
  , 2) AS relative_success
FROM tests
GROUP BY category, dimension
ORDER BY category, relative_success DESC NULLS LAST;

COMMENT ON VIEW preclin.v_relative_success IS
'Pheiron-style Relative Success (RS) per evidence dimension. RS = P(approved|supported)/P(approved|not-supported). Unit: T-I pair (Phase 2+). Baseline Nelson 2015 / Minikel 2024: rare Mendelian ~3.5.';

-- ============================================================
-- v_combination_evidence — 2-way evidence combinations
-- ============================================================
DROP VIEW IF EXISTS preclin.v_combination_evidence CASCADE;
CREATE VIEW preclin.v_combination_evidence AS
WITH ti_pool AS (
  SELECT ti.*, tw.*
  FROM preclin.v_target_indication_program ti
  JOIN preclin.v_target_evidence_wide tw ON tw.target_id = ti.target_id
  WHERE ti.max_phase_reached >= 2
),
combos AS (
  -- Combinations to test (Pheiron: genetic × tissue, genetic × pathway, etc.)
  SELECT 'Mendelian ≥5' AS ev_a, 'Tissue-specific (Tau ≥0.75)' AS ev_b,
         (mendelian_n >= 5) AS a, (tau_specificity >= 0.75) AS b, any_approved
    FROM ti_pool WHERE mendelian_n IS NOT NULL AND tau_specificity IS NOT NULL
  UNION ALL
  SELECT 'ClinGen Strong/Def ≥1', 'Tissue-specific (Tau ≥0.75)',
         (clingen_n_strong >= 1), (tau_specificity >= 0.75), any_approved
    FROM ti_pool WHERE clingen_n_strong IS NOT NULL AND tau_specificity IS NOT NULL
  UNION ALL
  SELECT 'OT genetic ≥0.3', 'OT animal model ≥0.3',
         (ot_genetic_max >= 0.3), (ot_animal_model_max >= 0.3), any_approved
    FROM ti_pool WHERE ot_genetic_max IS NOT NULL AND ot_animal_model_max IS NOT NULL
  UNION ALL
  SELECT 'Mendelian ≥5', 'Line E lit high (≥2)',
         (mendelian_n >= 5), (line_e_lit >= 2), any_approved
    FROM ti_pool WHERE mendelian_n IS NOT NULL AND line_e_lit IS NOT NULL
  UNION ALL
  SELECT 'ClinGen Strong/Def ≥1', 'DepMap NOT pan-essential',
         (clingen_n_strong >= 1), (depmap_pan_essential IS FALSE), any_approved
    FROM ti_pool WHERE clingen_n_strong IS NOT NULL AND depmap_pan_essential IS NOT NULL
  UNION ALL
  SELECT 'OT genetic ≥0.3', 'Reactome pathways ≥5',
         (ot_genetic_max >= 0.3), (n_reactome_pathways >= 5), any_approved
    FROM ti_pool WHERE ot_genetic_max IS NOT NULL AND n_reactome_pathways IS NOT NULL
  UNION ALL
  SELECT 'Line C lit high (≥2)', 'Line D lit high (≥2)',
         (line_c_lit >= 2), (line_d_lit >= 2), any_approved
    FROM ti_pool WHERE line_c_lit IS NOT NULL AND line_d_lit IS NOT NULL
),
by_combo AS (
  SELECT
    ev_a, ev_b,
    -- Baseline RS for each alone
    COUNT(*) FILTER (WHERE a) AS n_a, COUNT(*) FILTER (WHERE a AND any_approved) AS n_a_appr,
    COUNT(*) FILTER (WHERE NOT a) AS n_not_a, COUNT(*) FILTER (WHERE NOT a AND any_approved) AS n_not_a_appr,
    COUNT(*) FILTER (WHERE b) AS n_b, COUNT(*) FILTER (WHERE b AND any_approved) AS n_b_appr,
    COUNT(*) FILTER (WHERE NOT b) AS n_not_b, COUNT(*) FILTER (WHERE NOT b AND any_approved) AS n_not_b_appr,
    -- Combined
    COUNT(*) FILTER (WHERE a AND b) AS n_ab, COUNT(*) FILTER (WHERE a AND b AND any_approved) AS n_ab_appr,
    COUNT(*) FILTER (WHERE NOT (a AND b)) AS n_not_ab, COUNT(*) FILTER (WHERE NOT (a AND b) AND any_approved) AS n_not_ab_appr
  FROM combos
  GROUP BY ev_a, ev_b
)
SELECT
  ev_a, ev_b,
  n_a, n_a_appr,
  ROUND(
    ((n_a_appr::numeric / NULLIF(n_a, 0)) /
     NULLIF(n_not_a_appr::numeric / NULLIF(n_not_a, 0), 0))
  , 2) AS rs_a_alone,
  n_b, n_b_appr,
  ROUND(
    ((n_b_appr::numeric / NULLIF(n_b, 0)) /
     NULLIF(n_not_b_appr::numeric / NULLIF(n_not_b, 0), 0))
  , 2) AS rs_b_alone,
  n_ab, n_ab_appr,
  ROUND(
    ((n_ab_appr::numeric / NULLIF(n_ab, 0)) /
     NULLIF(n_not_ab_appr::numeric / NULLIF(n_not_ab, 0), 0))
  , 2) AS rs_combined,
  ROUND(100.0 * n_ab_appr / NULLIF(n_ab, 0)::numeric, 1) AS combined_approval_pct
FROM by_combo
ORDER BY rs_combined DESC NULLS LAST;

COMMENT ON VIEW preclin.v_combination_evidence IS
'Pairwise evidence combinations. Compares RS(A alone), RS(B alone), RS(A AND B). Pheiron Figure 4-style.';

COMMIT;
