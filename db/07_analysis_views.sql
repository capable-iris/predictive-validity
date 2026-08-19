-- Clean analysis views — filter placebos, canonicalize sponsors.
-- Reapplied on top of 06_ti_views.sql, so run this LAST.

BEGIN;

-- Canonical sponsor name via public.sponsors (fallback to raw sponsor_name)
DROP VIEW IF EXISTS preclin.v_program_clean CASCADE;
CREATE VIEW preclin.v_program_clean AS
SELECT
  p.program_id, p.drug_id, p.indication_id, p.sponsor_id,
  COALESCE(sp.canonical_name, p.sponsor_name) AS sponsor_canonical,
  p.sponsor_name AS sponsor_raw,
  p.first_trial_date, p.last_trial_date, p.highest_phase,
  p.n_trials, p.n_trials_ph1, p.n_trials_ph2, p.n_trials_ph3, p.n_trials_ph4,
  p.n_terminated, p.n_withdrawn, p.n_suspended, p.n_completed, p.n_active,
  d.normalized_name AS drug_key, d.display_name AS drug_name,
  d.modality, d.resolved_via, d.is_placebo, d.is_combination
FROM preclin.program p
JOIN preclin.drug d ON d.drug_id = p.drug_id
LEFT JOIN public.sponsors sp ON sp.id = p.sponsor_id
WHERE d.is_placebo IS NOT TRUE;  -- filter placebos globally

COMMENT ON VIEW preclin.v_program_clean IS
'Program view with placebos excluded + canonical sponsor names. Use this for downstream analysis, not preclin.program directly.';

-- Re-apply RS metric using clean cohort (drop placebos)
DROP VIEW IF EXISTS preclin.v_relative_success_clean CASCADE;
CREATE VIEW preclin.v_relative_success_clean AS
WITH ti_pool AS (
  SELECT ti.*, tw.*
  FROM preclin.v_target_indication_program ti
  JOIN preclin.v_target_evidence_wide tw ON tw.target_id = ti.target_id
  -- Only include T-I pairs backed by ≥1 non-placebo program
  WHERE ti.max_phase_reached >= 2
    AND EXISTS (
      SELECT 1 FROM preclin.program p
      JOIN preclin.drug d ON d.drug_id = p.drug_id
      WHERE p.indication_id = ti.indication_id
        AND EXISTS (
          SELECT 1 FROM preclin.v_drug_target dt
          WHERE dt.drug_id = p.drug_id AND dt.target_id = ti.target_id
        )
        AND d.is_placebo IS NOT TRUE
    )
),
tests AS (
  SELECT 'A. Mendelian ≥5' AS dimension, 'A_genetics' AS category,
         (mendelian_n >= 5) AS supported, any_approved FROM ti_pool WHERE mendelian_n IS NOT NULL
  UNION ALL SELECT 'A. ClinGen Strong/Def ≥1', 'A_genetics',
    (clingen_n_strong >= 1), any_approved FROM ti_pool WHERE clingen_n_strong IS NOT NULL
  UNION ALL SELECT 'A. GWAS ≥50', 'A_genetics',
    (gwas_n_sig >= 50), any_approved FROM ti_pool WHERE gwas_n_sig IS NOT NULL
  UNION ALL SELECT 'A. OT genetic ≥0.3', 'A_genetics',
    (ot_genetic_max >= 0.3), any_approved FROM ti_pool WHERE ot_genetic_max IS NOT NULL
  UNION ALL SELECT 'A. OT somatic ≥0.3', 'A_genetics',
    (ot_somatic_score_max >= 0.3), any_approved FROM ti_pool WHERE ot_somatic_score_max IS NOT NULL
  UNION ALL SELECT 'A. HPO phenotype breadth ≥10', 'A_genetics',
    (n_hpo_phenotypes >= 10), any_approved FROM ti_pool WHERE n_hpo_phenotypes IS NOT NULL
  UNION ALL SELECT 'B. Tractable — small mol', 'B_mechanistic',
    (tractability_sm IS TRUE), any_approved FROM ti_pool WHERE tractability_sm IS NOT NULL
  UNION ALL SELECT 'B. Tractable — antibody', 'B_mechanistic',
    (tractability_ab IS TRUE), any_approved FROM ti_pool WHERE tractability_ab IS NOT NULL
  UNION ALL SELECT 'B. Bulk Tau ≥0.75', 'B_mechanistic',
    (tau_specificity >= 0.75), any_approved FROM ti_pool WHERE tau_specificity IS NOT NULL
  UNION ALL SELECT 'B. SC Tau ≥0.75', 'B_mechanistic',
    (sc_tau_specificity >= 0.75), any_approved FROM ti_pool WHERE sc_tau_specificity IS NOT NULL
  UNION ALL SELECT 'B. Reactome pathways ≥5', 'B_mechanistic',
    (n_reactome_pathways >= 5), any_approved FROM ti_pool WHERE n_reactome_pathways IS NOT NULL
  UNION ALL SELECT 'B. PPI hub (≥50 partners)', 'B_mechanistic',
    (n_ppi_partners >= 50), any_approved FROM ti_pool WHERE n_ppi_partners IS NOT NULL
  UNION ALL SELECT 'B. GO-BP ≥20 terms', 'B_mechanistic',
    (n_go_biological_process >= 20), any_approved FROM ti_pool WHERE n_go_biological_process IS NOT NULL
  UNION ALL SELECT 'C. Line C lit high (≥2)', 'C_cell',
    (line_c_lit >= 2), any_approved FROM ti_pool WHERE line_c_lit IS NOT NULL
  UNION ALL SELECT 'C. DepMap pan-essential', 'C_cell',
    (depmap_pan_essential IS TRUE), any_approved FROM ti_pool WHERE depmap_pan_essential IS NOT NULL
  UNION ALL SELECT 'D. Line D lit high (≥2)', 'D_animal',
    (line_d_lit >= 2), any_approved FROM ti_pool WHERE line_d_lit IS NOT NULL
  UNION ALL SELECT 'D. OT animal model ≥0.3', 'D_animal',
    (ot_animal_model_max >= 0.3), any_approved FROM ti_pool WHERE ot_animal_model_max IS NOT NULL
  UNION ALL SELECT 'D. IMPC ≥3 phenotypes', 'D_animal',
    (impc_n_phenotypes >= 3), any_approved FROM ti_pool WHERE impc_n_phenotypes IS NOT NULL
  UNION ALL SELECT 'E. Line E lit high (≥2)', 'E_pd',
    (line_e_lit >= 2), any_approved FROM ti_pool WHERE line_e_lit IS NOT NULL
  UNION ALL SELECT 'H. gnomAD pLI ≥0.9', 'H_safety',
    (gnomad_pli >= 0.9), any_approved FROM ti_pool WHERE gnomad_pli IS NOT NULL
  UNION ALL SELECT 'H. gnomAD LOEUF <0.35', 'H_safety',
    (gnomad_loeuf < 0.35), any_approved FROM ti_pool WHERE gnomad_loeuf IS NOT NULL
  UNION ALL SELECT 'I. Causal disease pleiotropy ≥3', 'I_landscape',
    (n_causal_diseases >= 3), any_approved FROM ti_pool WHERE n_causal_diseases IS NOT NULL
  UNION ALL SELECT 'I. DGIdb drug precedent ≥5', 'I_landscape',
    (n_dgidb_drugs >= 5), any_approved FROM ti_pool WHERE n_dgidb_drugs IS NOT NULL
)
SELECT
  category, dimension,
  COUNT(*) FILTER (WHERE supported) AS n_supported,
  COUNT(*) FILTER (WHERE NOT supported) AS n_not_supported,
  COUNT(*) FILTER (WHERE supported AND any_approved) AS n_supported_approved,
  COUNT(*) FILTER (WHERE NOT supported AND any_approved) AS n_not_supported_approved,
  ROUND(100.0 * COUNT(*) FILTER (WHERE supported AND any_approved)
              / NULLIF(COUNT(*) FILTER (WHERE supported), 0), 1) AS supported_pct_approved,
  ROUND(100.0 * COUNT(*) FILTER (WHERE NOT supported AND any_approved)
              / NULLIF(COUNT(*) FILTER (WHERE NOT supported), 0), 1) AS not_supported_pct_approved,
  ROUND(
    ((COUNT(*) FILTER (WHERE supported AND any_approved)::numeric / NULLIF(COUNT(*) FILTER (WHERE supported), 0))
     /
     NULLIF(COUNT(*) FILTER (WHERE NOT supported AND any_approved)::numeric / NULLIF(COUNT(*) FILTER (WHERE NOT supported), 0), 0))
    , 2) AS relative_success
FROM tests
GROUP BY category, dimension
ORDER BY category, relative_success DESC NULLS LAST;

COMMENT ON VIEW preclin.v_relative_success_clean IS
'Pheiron RS at T-I unit, placebos excluded, all newest dimensions included.';

-- Coverage matrix — everything per dimension
DROP VIEW IF EXISTS preclin.v_dimension_coverage CASCADE;
CREATE VIEW preclin.v_dimension_coverage AS
SELECT
  ed.category, ed.dimension, ed.description,
  ed.data_type, ed.subject_type,
  COUNT(DISTINCT es.subject_id) AS n_subjects_covered,
  MIN(es.value_numeric) AS min_val,
  MAX(es.value_numeric) AS max_val,
  ROUND(AVG(es.value_numeric)::numeric, 3) AS mean_val,
  MIN(es.extracted_at) AS first_extraction,
  MAX(es.extracted_at) AS last_extraction
FROM preclin.evidence_dimension ed
LEFT JOIN preclin.evidence_score es ON es.dimension = ed.dimension
GROUP BY ed.category, ed.dimension, ed.description, ed.data_type, ed.subject_type
ORDER BY ed.category, n_subjects_covered DESC NULLS LAST;

COMMENT ON VIEW preclin.v_dimension_coverage IS
'For every evidence dimension: how many subjects covered, value range, extraction dates.';

COMMIT;
