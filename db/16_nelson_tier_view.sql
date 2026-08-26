-- Migrate the existing master program view to audited cohort-wide Nelson tiers.
--
-- CREATE OR REPLACE preserves dependent views. Do not rerun 03_views.sql on
-- an established database: its bootstrap DROP ... CASCADE statements would
-- remove views installed by later setup files.

BEGIN;

CREATE OR REPLACE VIEW preclin.v_program_evidence_wide AS
WITH primary_targets AS (
  SELECT drug_id, target_id, target_symbol
  FROM preclin.v_drug_target_consensus
  WHERE role = 'primary'
),
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
tgt_ind_ev AS (
  SELECT DISTINCT ON (subject_id, subject_id2)
    subject_id AS target_id,
    subject_id2 AS indication_id,
    value_text AS nelson_tier
  FROM preclin.evidence_score
  WHERE subject_type = 'target_indication'
    AND dimension = 'nelson_tier'
    AND source = 'nelson_llm'
  ORDER BY subject_id, subject_id2, extracted_at DESC, evidence_id DESC
),
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
  FROM public.clingen_validity
  GROUP BY target_id
),
gb_mendelian AS (
  SELECT target_id, COUNT(*) AS mendelian_n
  FROM public.mendelian_associations
  GROUP BY target_id
),
gb_gwas AS (
  SELECT target_id,
         COUNT(*) FILTER (WHERE p_value < 5e-8) AS gwas_n_sig
  FROM public.gwas_associations
  GROUP BY target_id
),
gb_te AS (
  SELECT target_id,
    MAX(overall_score) AS ot_overall_max,
    MAX(genetic_score) AS ot_genetic_max,
    MAX(animal_model_score) AS ot_animal_model_max,
    MAX(known_drug_score) AS ot_known_drug_max,
    COUNT(DISTINCT disease_id) AS ot_n_diseases
  FROM public.target_evidence
  GROUP BY target_id
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
  ti.nelson_tier,
  te.line_b_lit, te.line_c_lit, te.line_d_lit, te.line_e_lit,
  te.impc_n_phenotypes, te.family_approved_count, te.gene_approved_count,
  de.drug_cell_efficacy, de.drug_rodent_efficacy, de.drug_nonrodent_efficacy,
  de.drug_target_engagement, de.drug_structural_biology, de.drug_tox_signal,
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
LEFT JOIN tgt_ind_ev ti
  ON ti.target_id = pt.target_id AND ti.indication_id = p.indication_id
LEFT JOIN target_ev te ON te.target_id = pt.target_id
LEFT JOIN drug_ev de ON de.drug_id = p.drug_id
LEFT JOIN gb_gene gg ON gg.target_id = pt.target_id
LEFT JOIN gb_clingen gc ON gc.target_id = pt.target_id
LEFT JOIN gb_mendelian gm ON gm.target_id = pt.target_id
LEFT JOIN gb_gwas gw ON gw.target_id = pt.target_id
LEFT JOIN gb_te gt ON gt.target_id = pt.target_id
LEFT JOIN gb_sider gs ON gs.therapy_id = d.therapy_id;

COMMENT ON VIEW preclin.v_program_evidence_wide IS
'Master analysis view. One row per program with primary target + all evidence dimensions joined. Nelson tier resolves only from cohort-wide source=nelson_llm rows; legacy approval-derived tiers remain audit-only.';

COMMIT;
