-- Strict per-(target × indication) outcome view.
-- Fixes the caveat that any_approved was matching any indication for that drug/target.
-- v_target_indication_strict_outcome: approved = "this drug approved FOR THIS INDICATION"

BEGIN;

DROP VIEW IF EXISTS preclin.v_target_indication_strict_outcome CASCADE;
CREATE VIEW preclin.v_target_indication_strict_outcome AS
WITH ti_program_outcomes AS (
  -- For each (T-I), each program's outcome for THAT indication
  SELECT
    dt.target_id, p.indication_id, p.program_id, p.sponsor_name,
    p.highest_phase, p.first_trial_date, p.last_trial_date,
    po.outcome, po.outcome_broad, po.approved_us, po.approved_ex_us,
    -- Was THIS drug approved FOR THIS INDICATION specifically?
    EXISTS (
      SELECT 1 FROM preclin.approval a
      WHERE a.drug_id = p.drug_id AND a.indication_id = p.indication_id
    ) AS approved_this_indication
  FROM preclin.program p
  JOIN preclin.v_drug_target_consensus dt
    ON dt.drug_id = p.drug_id AND dt.role = 'primary'
  JOIN preclin.drug d ON d.drug_id = p.drug_id
  JOIN public.targets t ON t.id = dt.target_id
  JOIN preclin.program_outcome po ON po.program_id = p.program_id
  WHERE d.is_placebo IS NOT TRUE
    AND (t.pathogen_type IS NULL OR t.pathogen_type = '')
    AND t.ip_type IS DISTINCT FROM 'Genomic'
),
ti_rollup AS (
  SELECT
    target_id, indication_id,
    COUNT(*) AS n_programs,
    COUNT(DISTINCT sponsor_name) AS n_sponsors,
    MAX(highest_phase) AS max_phase_reached,
    MIN(first_trial_date) AS first_trial_date,
    MAX(last_trial_date) AS last_trial_date,
    -- Strict: approved specifically for this T-I
    BOOL_OR(approved_this_indication) AS strict_approved_this_ti,
    -- Loose: any approval on this drug (any indication)
    BOOL_OR(approved_us OR approved_ex_us) AS loose_approved_any_indication,
    BOOL_OR(outcome = 'efficacy_fail' OR outcome_broad = 'presumptive_efficacy_fail_ph3')
      AS any_efficacy_fail,
    BOOL_OR(outcome = 'safety_fail') AS any_safety_fail,
    STRING_AGG(DISTINCT outcome_broad, '|' ORDER BY outcome_broad) AS outcomes_broad_all
  FROM ti_program_outcomes
  GROUP BY target_id, indication_id
)
SELECT * FROM ti_rollup;

COMMENT ON VIEW preclin.v_target_indication_strict_outcome IS
'Target-indication outcomes from non-placebo programs with one uniquely best-supported primary-target consensus. Strict means approved for THIS indication; loose means any approval on the drug.';

COMMIT;
