-- Analysis views over preclin.* + public.*
-- Rebuild whenever schema changes.

BEGIN;

-- ============================================================
-- v_drug_target — best (highest-confidence) target per drug
-- ============================================================
DROP VIEW IF EXISTS preclin.v_drug_target CASCADE;
CREATE VIEW preclin.v_drug_target AS
SELECT DISTINCT ON (dt.drug_id, dt.role)
  dt.drug_id, dt.target_id, dt.role, dt.mechanism, dt.source, dt.confidence,
  t.symbol AS target_symbol, t.family AS target_family, t.tdl AS target_tdl
FROM preclin.drug_target dt
JOIN public.targets t ON t.id = dt.target_id
ORDER BY dt.drug_id, dt.role,
  CASE dt.source
    WHEN 'fda_approval'            THEN 1
    WHEN 'llm_sonnet_verified'     THEN 2
    WHEN 'therapy_targets_public'  THEN 3
    WHEN 'chembl_bulk'             THEN 4
    WHEN 'llm_sonnet'              THEN 5
    WHEN 'llm_haiku'               THEN 6
    ELSE 99
  END;

COMMENT ON VIEW preclin.v_drug_target IS 'Best target per (drug, role). Priority: FDA > Sonnet-verified > public.therapy_targets > ChEMBL > LLM.';

-- ============================================================
-- v_program_evidence_wide — one row per program with all evidence dims
-- ============================================================
DROP VIEW IF EXISTS preclin.v_program_evidence_wide CASCADE;
CREATE VIEW preclin.v_program_evidence_wide AS
WITH primary_targets AS (
  SELECT drug_id, target_id, target_symbol
  FROM preclin.v_drug_target
  WHERE role = 'primary'
),
-- Target-level evidence pivoted
target_ev AS (
  SELECT
    subject_id AS target_id,
    MAX(CASE WHEN dimension = 'line_b_lit' THEN value_numeric END) AS line_b_lit,
    MAX(CASE WHEN dimension = 'line_c_lit' THEN value_numeric END) AS line_c_lit,
    MAX(CASE WHEN dimension = 'line_d_lit' THEN value_numeric END) AS line_d_lit,
    MAX(CASE WHEN dimension = 'line_e_lit' THEN value_numeric END) AS line_e_lit,
    MAX(CASE WHEN dimension = 'impc_n_phenotypes' THEN value_numeric END) AS impc_n_phenotypes,
    MAX(CASE WHEN dimension = 'family_approved_count' THEN value_numeric END) AS family_approved_count,
    MAX(CASE WHEN dimension = 'gene_approved_count' THEN value_numeric END) AS gene_approved_count
  FROM preclin.evidence_score
  WHERE subject_type = 'target'
  GROUP BY subject_id
),
-- Drug-level evidence pivoted
drug_ev AS (
  SELECT
    subject_id AS drug_id,
    MAX(CASE WHEN dimension = 'drug_cell_efficacy' THEN value_numeric END) AS drug_cell_efficacy,
    MAX(CASE WHEN dimension = 'drug_rodent_efficacy' THEN value_numeric END) AS drug_rodent_efficacy,
    MAX(CASE WHEN dimension = 'drug_nonrodent_efficacy' THEN value_numeric END) AS drug_nonrodent_efficacy,
    MAX(CASE WHEN dimension = 'drug_target_engagement' THEN value_numeric END) AS drug_target_engagement,
    MAX(CASE WHEN dimension = 'drug_structural_biology' THEN value_numeric END) AS drug_structural_biology,
    MAX(CASE WHEN dimension = 'drug_tox_signal' THEN value_numeric END) AS drug_tox_signal
  FROM preclin.evidence_score
  WHERE subject_type = 'drug'
  GROUP BY subject_id
),
-- Nelson tier per (target, indication)
tgt_ind_ev AS (
  SELECT DISTINCT ON (subject_id, subject_id2)
    subject_id AS target_id, subject_id2 AS indication_id,
    value_text AS nelson_tier
  FROM preclin.evidence_score
  WHERE subject_type = 'target_indication'
    AND dimension = 'nelson_tier'
    AND source = 'nelson_llm'
  ORDER BY subject_id, subject_id2, extracted_at DESC, evidence_id DESC
),
-- Genome-browser DB-native scores (read directly)
gb_gene AS (
  SELECT
    t.id AS target_id,
    gc.pli AS gnomad_pli,
    gc.loeuf AS gnomad_loeuf,
    ges.mean_effect AS depmap_mean_effect,
    ges.pan_essential AS depmap_pan_essential,
    ges.n_dependent_lineages AS depmap_n_dep_lineages,
    ges.most_dependent_lineage AS depmap_top_lineage,
    t.family, t.tdl,
    t.tractability_sm, t.tractability_ab, t.tractability_protac
  FROM public.targets t
  LEFT JOIN public.gene_constraint gc ON gc.target_id = t.id
  LEFT JOIN public.gene_essentiality_summary ges ON ges.target_id = t.id
),
gb_clingen AS (
  SELECT target_id,
         COUNT(*) FILTER (WHERE classification IN ('Definitive','Strong')) AS clingen_n_strong,
         COUNT(*) AS clingen_n_all
  FROM public.clingen_validity GROUP BY target_id
),
gb_mendelian AS (
  SELECT target_id, COUNT(*) AS mendelian_n
  FROM public.mendelian_associations GROUP BY target_id
),
gb_gwas AS (
  SELECT target_id, COUNT(*) FILTER (WHERE p_value < 5e-8) AS gwas_n_sig
  FROM public.gwas_associations GROUP BY target_id
),
gb_te AS (
  SELECT target_id,
    MAX(overall_score) AS ot_overall_max,
    MAX(genetic_score) AS ot_genetic_max,
    MAX(animal_model_score) AS ot_animal_model_max,
    MAX(known_drug_score) AS ot_known_drug_max,
    COUNT(DISTINCT disease_id) AS ot_n_diseases
  FROM public.target_evidence GROUP BY target_id
),
gb_sider AS (
  SELECT th.id AS therapy_id,
    COUNT(ae.meddra_id) AS sider_n_ae,
    COUNT(DISTINCT ae.meddra_id) AS sider_n_uniq_ae
  FROM public.therapies th
  LEFT JOIN public.adverse_events ae ON ae.therapy_id = th.id
  GROUP BY th.id
)
SELECT
  p.program_id, p.drug_id, p.indication_id, p.sponsor_id, p.sponsor_name,
  d.normalized_name AS drug_key, d.display_name AS drug_name,
  d.modality, d.resolved_via,
  i.display_name AS indication, i.therapeutic_area,
  pt.target_id, pt.target_symbol,
  po.outcome, po.outcome_broad, po.outcome_confidence,
  po.approved_us, po.approved_ex_us, po.failure_reasons,
  p.highest_phase, p.n_trials, p.n_trials_ph2, p.n_trials_ph3,
  p.n_completed, p.n_terminated,
  -- Nelson tier
  ti.nelson_tier,
  -- Target-level lit scores
  te.line_b_lit, te.line_c_lit, te.line_d_lit, te.line_e_lit,
  te.impc_n_phenotypes, te.family_approved_count, te.gene_approved_count,
  -- Drug-specific
  de.drug_cell_efficacy, de.drug_rodent_efficacy, de.drug_nonrodent_efficacy,
  de.drug_target_engagement, de.drug_structural_biology, de.drug_tox_signal,
  -- Gene-level (genome-browser native)
  gg.gnomad_pli, gg.gnomad_loeuf, gg.depmap_mean_effect,
  gg.depmap_pan_essential, gg.depmap_n_dep_lineages, gg.depmap_top_lineage,
  gg.family AS target_family, gg.tdl AS target_tdl,
  gg.tractability_sm, gg.tractability_ab, gg.tractability_protac,
  gc.clingen_n_strong, gc.clingen_n_all,
  gm.mendelian_n,
  gw.gwas_n_sig,
  gt.ot_overall_max, gt.ot_genetic_max, gt.ot_animal_model_max,
  gt.ot_known_drug_max, gt.ot_n_diseases,
  gs.sider_n_ae, gs.sider_n_uniq_ae
FROM preclin.program p
JOIN preclin.drug d ON d.drug_id = p.drug_id
JOIN preclin.indication i ON i.indication_id = p.indication_id
JOIN preclin.program_outcome po ON po.program_id = p.program_id
LEFT JOIN primary_targets pt ON pt.drug_id = p.drug_id
LEFT JOIN tgt_ind_ev ti ON ti.target_id = pt.target_id AND ti.indication_id = p.indication_id
LEFT JOIN target_ev te ON te.target_id = pt.target_id
LEFT JOIN drug_ev de ON de.drug_id = p.drug_id
LEFT JOIN gb_gene gg ON gg.target_id = pt.target_id
LEFT JOIN gb_clingen gc ON gc.target_id = pt.target_id
LEFT JOIN gb_mendelian gm ON gm.target_id = pt.target_id
LEFT JOIN gb_gwas gw ON gw.target_id = pt.target_id
LEFT JOIN gb_te gt ON gt.target_id = pt.target_id
LEFT JOIN gb_sider gs ON gs.therapy_id = d.therapy_id;

COMMENT ON VIEW preclin.v_program_evidence_wide IS
'Master analysis view. One row per program with primary target + all evidence dimensions joined. Replaces drug_evidence_master_v2_broad.csv.';

-- ============================================================
-- v_pathway_wrongness — Phase 3 fail rate per evidence tier
-- ============================================================
DROP VIEW IF EXISTS preclin.v_pathway_wrongness CASCADE;
CREATE VIEW preclin.v_pathway_wrongness AS
WITH ph3 AS (
  SELECT * FROM preclin.v_program_evidence_wide WHERE highest_phase >= 3 AND target_id IS NOT NULL
),
tests AS (
  SELECT 'Line C (cell lit) high (≥2)' AS dimension,
         (line_c_lit >= 2) AS is_high, outcome_broad FROM ph3 WHERE line_c_lit IS NOT NULL
  UNION ALL
  SELECT 'Line D (animal lit) high (≥2)', (line_d_lit >= 2), outcome_broad FROM ph3 WHERE line_d_lit IS NOT NULL
  UNION ALL
  SELECT 'Line E (human PD) high (≥2)', (line_e_lit >= 2), outcome_broad FROM ph3 WHERE line_e_lit IS NOT NULL
  UNION ALL
  SELECT 'ClinGen Strong/Def ≥1', (clingen_n_strong >= 1), outcome_broad FROM ph3 WHERE clingen_n_strong IS NOT NULL
  UNION ALL
  SELECT 'Mendelian ≥5', (mendelian_n >= 5), outcome_broad FROM ph3 WHERE mendelian_n IS NOT NULL
  UNION ALL
  SELECT 'OT genetic ≥0.3', (ot_genetic_max >= 0.3), outcome_broad FROM ph3 WHERE ot_genetic_max IS NOT NULL
  UNION ALL
  SELECT 'OT animal model ≥0.3', (ot_animal_model_max >= 0.3), outcome_broad FROM ph3 WHERE ot_animal_model_max IS NOT NULL
  UNION ALL
  SELECT 'IMPC ≥3 KO phenotypes', (impc_n_phenotypes >= 3), outcome_broad FROM ph3 WHERE impc_n_phenotypes IS NOT NULL
  UNION ALL
  SELECT 'DepMap pan-essential', (depmap_pan_essential IS TRUE), outcome_broad FROM ph3 WHERE depmap_pan_essential IS NOT NULL
  UNION ALL
  SELECT 'gnomAD pLI ≥0.9', (gnomad_pli >= 0.9), outcome_broad FROM ph3 WHERE gnomad_pli IS NOT NULL
)
SELECT
  dimension,
  is_high AS is_high_evidence,
  COUNT(*) AS n_total,
  COUNT(*) FILTER (WHERE outcome_broad = 'approved') AS n_approved,
  COUNT(*) FILTER (WHERE outcome_broad IN ('efficacy_fail', 'presumptive_efficacy_fail_ph3')) AS n_efficacy_fail,
  COUNT(*) FILTER (WHERE outcome_broad = 'safety_fail') AS n_safety_fail,
  COUNT(*) FILTER (WHERE outcome_broad = 'commercial_fail') AS n_commercial_fail,
  COUNT(*) FILTER (WHERE outcome_broad != 'approved') AS n_any_fail,
  ROUND(100.0 * COUNT(*) FILTER (WHERE outcome_broad = 'approved') / NULLIF(COUNT(*), 0), 1) AS approved_pct,
  ROUND(100.0 * COUNT(*) FILTER (WHERE outcome_broad IN ('efficacy_fail','presumptive_efficacy_fail_ph3')) / NULLIF(COUNT(*), 0), 1) AS efficacy_fail_pct,
  ROUND(100.0 * COUNT(*) FILTER (WHERE outcome_broad != 'approved') / NULLIF(COUNT(*), 0), 1) AS any_fail_pct
FROM tests
GROUP BY dimension, is_high
ORDER BY dimension, is_high DESC;

COMMENT ON VIEW preclin.v_pathway_wrongness IS
'Q: how often does strong evidence at Phase 3 still fail? Replaces pathway_wrongness.py.';

-- ============================================================
-- v_effect_sizes — 2x2 counts per dimension for OR + CI
-- ============================================================
DROP VIEW IF EXISTS preclin.v_effect_sizes_2x2 CASCADE;
CREATE VIEW preclin.v_effect_sizes_2x2 AS
WITH resolved AS (
  SELECT * FROM preclin.v_program_evidence_wide
  WHERE outcome_broad IN ('approved','efficacy_fail','safety_fail','commercial_fail',
                          'enrollment_fail','presumptive_efficacy_fail_ph3','presumptive_fail_ph2')
    AND target_id IS NOT NULL
),
tight AS (
  SELECT * FROM preclin.v_program_evidence_wide
  WHERE outcome_broad IN ('approved','efficacy_fail','safety_fail','commercial_fail','enrollment_fail')
    AND target_id IS NOT NULL
),
tests AS (
  SELECT 'Line C lit high (≥2)' AS dimension, (line_c_lit >= 2) AS is_high, outcome_broad, 'broad' AS cohort FROM resolved WHERE line_c_lit IS NOT NULL
  UNION ALL SELECT 'Line D lit high (≥2)', (line_d_lit >= 2), outcome_broad, 'broad' FROM resolved WHERE line_d_lit IS NOT NULL
  UNION ALL SELECT 'Line E lit high (≥2)', (line_e_lit >= 2), outcome_broad, 'broad' FROM resolved WHERE line_e_lit IS NOT NULL
  UNION ALL SELECT 'ClinGen Strong/Def ≥1', (clingen_n_strong >= 1), outcome_broad, 'broad' FROM resolved WHERE clingen_n_strong IS NOT NULL
  UNION ALL SELECT 'Mendelian ≥5', (mendelian_n >= 5), outcome_broad, 'broad' FROM resolved WHERE mendelian_n IS NOT NULL
  UNION ALL SELECT 'OT genetic ≥0.3', (ot_genetic_max >= 0.3), outcome_broad, 'broad' FROM resolved WHERE ot_genetic_max IS NOT NULL
  UNION ALL SELECT 'OT overall ≥0.5', (ot_overall_max >= 0.5), outcome_broad, 'broad' FROM resolved WHERE ot_overall_max IS NOT NULL
  UNION ALL SELECT 'OT animal model ≥0.3', (ot_animal_model_max >= 0.3), outcome_broad, 'broad' FROM resolved WHERE ot_animal_model_max IS NOT NULL
  UNION ALL SELECT 'DepMap pan-essential', (depmap_pan_essential IS TRUE), outcome_broad, 'broad' FROM resolved WHERE depmap_pan_essential IS NOT NULL
  UNION ALL SELECT 'DepMap ≥5 dep lineages', (depmap_n_dep_lineages >= 5), outcome_broad, 'broad' FROM resolved WHERE depmap_n_dep_lineages IS NOT NULL
  UNION ALL SELECT 'gnomAD pLI ≥0.9', (gnomad_pli >= 0.9), outcome_broad, 'broad' FROM resolved WHERE gnomad_pli IS NOT NULL
  UNION ALL SELECT 'gnomAD LOEUF <0.35', (gnomad_loeuf < 0.35), outcome_broad, 'broad' FROM resolved WHERE gnomad_loeuf IS NOT NULL
  UNION ALL SELECT 'IMPC ≥3 phenotypes', (impc_n_phenotypes >= 3), outcome_broad, 'broad' FROM resolved WHERE impc_n_phenotypes IS NOT NULL
  UNION ALL SELECT 'Tractable — small mol', (tractability_sm IS TRUE), outcome_broad, 'broad' FROM resolved WHERE tractability_sm IS NOT NULL
  UNION ALL SELECT 'Tractable — antibody', (tractability_ab IS TRUE), outcome_broad, 'broad' FROM resolved WHERE tractability_ab IS NOT NULL
  -- Tight versions
  UNION ALL SELECT 'Line C lit high (≥2)', (line_c_lit >= 2), outcome_broad, 'tight' FROM tight WHERE line_c_lit IS NOT NULL
  UNION ALL SELECT 'Line D lit high (≥2)', (line_d_lit >= 2), outcome_broad, 'tight' FROM tight WHERE line_d_lit IS NOT NULL
  UNION ALL SELECT 'Line E lit high (≥2)', (line_e_lit >= 2), outcome_broad, 'tight' FROM tight WHERE line_e_lit IS NOT NULL
  UNION ALL SELECT 'ClinGen Strong/Def ≥1', (clingen_n_strong >= 1), outcome_broad, 'tight' FROM tight WHERE clingen_n_strong IS NOT NULL
  UNION ALL SELECT 'Mendelian ≥5', (mendelian_n >= 5), outcome_broad, 'tight' FROM tight WHERE mendelian_n IS NOT NULL
  UNION ALL SELECT 'DepMap pan-essential', (depmap_pan_essential IS TRUE), outcome_broad, 'tight' FROM tight WHERE depmap_pan_essential IS NOT NULL
  UNION ALL SELECT 'OT animal model ≥0.3', (ot_animal_model_max >= 0.3), outcome_broad, 'tight' FROM tight WHERE ot_animal_model_max IS NOT NULL
)
SELECT
  cohort, dimension,
  COUNT(*) FILTER (WHERE is_high AND outcome_broad = 'approved') AS high_approved,
  COUNT(*) FILTER (WHERE is_high AND outcome_broad != 'approved') AS high_failed,
  COUNT(*) FILTER (WHERE NOT is_high AND outcome_broad = 'approved') AS low_approved,
  COUNT(*) FILTER (WHERE NOT is_high AND outcome_broad != 'approved') AS low_failed,
  -- Point OR (add 0.5 continuity if any cell is 0)
  ROUND(
    CASE
      WHEN COUNT(*) FILTER (WHERE is_high AND outcome_broad = 'approved') > 0
        AND COUNT(*) FILTER (WHERE is_high AND outcome_broad != 'approved') > 0
        AND COUNT(*) FILTER (WHERE NOT is_high AND outcome_broad = 'approved') > 0
        AND COUNT(*) FILTER (WHERE NOT is_high AND outcome_broad != 'approved') > 0
      THEN (
        (COUNT(*) FILTER (WHERE is_high AND outcome_broad = 'approved')::float
         * COUNT(*) FILTER (WHERE NOT is_high AND outcome_broad != 'approved'))
        /
        NULLIF(COUNT(*) FILTER (WHERE is_high AND outcome_broad != 'approved')::float
               * COUNT(*) FILTER (WHERE NOT is_high AND outcome_broad = 'approved'), 0)
      )
      ELSE NULL
    END::numeric, 2
  ) AS odds_ratio_point
FROM tests
GROUP BY cohort, dimension
ORDER BY cohort, odds_ratio_point DESC NULLS LAST;

COMMENT ON VIEW preclin.v_effect_sizes_2x2 IS
'Point OR + 2x2 counts per evidence dimension. Bootstrap CIs computed offline into effect_size_snapshot.';

-- ============================================================
-- v_failure_taxonomy — distribution of failure reasons
-- ============================================================
DROP VIEW IF EXISTS preclin.v_failure_taxonomy CASCADE;
CREATE VIEW preclin.v_failure_taxonomy AS
WITH classified AS (
  SELECT DISTINCT ON (subject_key)
    subject_key AS nct_id, category, classifier_model, confidence
  FROM preclin.classification
  WHERE classifier_task = 'why_stopped' AND subject_type = 'trial'
  ORDER BY subject_key, CASE classifier_model WHEN 'claude-sonnet' THEN 1 ELSE 2 END
)
SELECT
  category,
  COUNT(*) AS n_trials,
  ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct_of_all_classified,
  COUNT(*) FILTER (WHERE confidence = 'high') AS n_high_confidence,
  COUNT(*) FILTER (WHERE classifier_model = 'claude-sonnet') AS n_sonnet
FROM classified
GROUP BY category
ORDER BY n_trials DESC;

COMMENT ON VIEW preclin.v_failure_taxonomy IS
'Trial-level failure reason distribution. Sonnet preferred over Haiku when both exist.';

-- ============================================================
-- v_outcome_summary — the top-line "state of the audit"
-- ============================================================
DROP VIEW IF EXISTS preclin.v_outcome_summary CASCADE;
CREATE VIEW preclin.v_outcome_summary AS
SELECT
  outcome_broad,
  COUNT(*) AS n_programs,
  ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct,
  COUNT(*) FILTER (WHERE outcome_confidence = 'high') AS n_high_conf,
  COUNT(DISTINCT drug_id) AS n_unique_drugs
FROM preclin.program_outcome po
JOIN preclin.program p ON p.program_id = po.program_id
GROUP BY outcome_broad
ORDER BY n_programs DESC;

-- ============================================================
-- v_drug_coverage — how well-characterized is each drug?
-- ============================================================
DROP VIEW IF EXISTS preclin.v_drug_coverage CASCADE;
CREATE VIEW preclin.v_drug_coverage AS
SELECT
  d.drug_id, d.normalized_name, d.resolved_via,
  COUNT(DISTINCT dt.target_id) AS n_targets,
  COUNT(DISTINCT p.program_id) AS n_programs,
  COUNT(DISTINCT pt.nct_id) AS n_trials,
  BOOL_OR(po.approved_us) AS approved_us,
  BOOL_OR(po.approved_ex_us) AS approved_ex_us,
  MAX(p.highest_phase) AS highest_phase_reached
FROM preclin.drug d
LEFT JOIN preclin.drug_target dt ON dt.drug_id = d.drug_id
LEFT JOIN preclin.program p ON p.drug_id = d.drug_id
LEFT JOIN preclin.program_trial pt ON pt.program_id = p.program_id
LEFT JOIN preclin.program_outcome po ON po.program_id = p.program_id
GROUP BY d.drug_id, d.normalized_name, d.resolved_via;

COMMIT;
