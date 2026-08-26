-- Replace arbitrary drug-to-target attribution with evidence-based consensus.
--
-- The legacy v_drug_target view uses DISTINCT ON without a target tie-break:
-- when equally preferred source rows name different primary targets, a query
-- plan can select either target. This migration first preserves the strict
-- unambiguous subset, then recovers cases with one uniquely best corroborated
-- target. Only unresolved strongest ties remain excluded.

BEGIN;

CREATE OR REPLACE VIEW preclin.v_drug_target_unambiguous AS
WITH ranked AS MATERIALIZED (
  SELECT dt.drug_id, dt.target_id, dt.role, dt.mechanism, dt.source,
    dt.confidence,
    CASE dt.source
      WHEN 'fda_approval'            THEN 1
      WHEN 'llm_sonnet_verified'     THEN 2
      WHEN 'therapy_targets_public'  THEN 3
      WHEN 'chembl_bulk'             THEN 4
      WHEN 'llm_sonnet'              THEN 5
      WHEN 'llm_haiku'               THEN 6
      ELSE 99
    END AS source_priority
  FROM preclin.drug_target dt
), best AS (
  SELECT *, MIN(source_priority) OVER (PARTITION BY drug_id, role) AS best_priority
  FROM ranked
), resolved AS (
  SELECT drug_id, role, MIN(target_id) AS target_id
  FROM best
  WHERE source_priority = best_priority
  GROUP BY drug_id, role
  HAVING COUNT(DISTINCT target_id) = 1
)
SELECT DISTINCT ON (b.drug_id, b.role)
  b.drug_id, b.target_id, b.role, b.mechanism, b.source, b.confidence,
  t.symbol AS target_symbol, t.family AS target_family, t.tdl AS target_tdl
FROM best b
JOIN resolved r USING (drug_id, role, target_id)
JOIN public.targets t ON t.id = b.target_id
WHERE b.source_priority = b.best_priority
ORDER BY b.drug_id, b.role, b.target_id, b.source,
  CASE b.confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END,
  b.mechanism NULLS LAST;

COMMENT ON VIEW preclin.v_drug_target_unambiguous IS
'Best-priority drug-target mapping only when all equally preferred rows agree on one target; ambiguous drugs are excluded from target-level outcome attribution.';

-- Build a persistent, indexed consensus snapshot in stages. The map is
-- refreshed transactionally by this migration, so readers never see a
-- partially populated target assignment.
CREATE TABLE IF NOT EXISTS preclin.drug_target_consensus_map (
  drug_id INTEGER NOT NULL REFERENCES preclin.drug(drug_id) ON DELETE CASCADE,
  target_id INTEGER NOT NULL REFERENCES public.targets(id),
  role TEXT NOT NULL,
  mechanism TEXT,
  source TEXT NOT NULL,
  confidence TEXT,
  target_symbol TEXT,
  target_family TEXT,
  target_tdl TEXT,
  refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (drug_id, role)
);
CREATE INDEX IF NOT EXISTS idx_drug_target_consensus_map_target
  ON preclin.drug_target_consensus_map (target_id);

CREATE TEMP TABLE dt_consensus_ranked ON COMMIT DROP AS
SELECT dt.drug_id, dt.target_id, dt.role, dt.mechanism, dt.source, dt.confidence,
  CASE dt.source
    WHEN 'fda_approval' THEN 1 WHEN 'llm_sonnet_verified' THEN 2
    WHEN 'therapy_targets_public' THEN 3 WHEN 'chembl_bulk' THEN 4
    WHEN 'llm_sonnet' THEN 5 WHEN 'llm_haiku' THEN 6 ELSE 99
  END AS source_priority,
  CASE dt.confidence
    WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4
  END AS confidence_priority
FROM preclin.drug_target dt
WHERE dt.source <> 'fda_approval';
CREATE INDEX ON dt_consensus_ranked (drug_id, role, target_id);

CREATE TEMP TABLE dt_consensus_target_source ON COMMIT DROP AS
SELECT drug_id, role, target_id,
  MIN(source_priority) AS target_best_source,
  COUNT(DISTINCT source) AS independent_sources
FROM dt_consensus_ranked
GROUP BY drug_id, role, target_id;
CREATE INDEX ON dt_consensus_target_source (drug_id, role, target_id);

CREATE TEMP TABLE dt_consensus_best_source ON COMMIT DROP AS
SELECT drug_id, role, MIN(target_best_source) AS best_source
FROM dt_consensus_target_source
GROUP BY drug_id, role;
CREATE INDEX ON dt_consensus_best_source (drug_id, role);

CREATE TEMP TABLE dt_consensus_candidates ON COMMIT DROP AS
SELECT s.drug_id, s.role, s.target_id, s.target_best_source,
  s.independent_sources, MIN(r.confidence_priority) AS target_best_confidence
FROM dt_consensus_target_source s
JOIN dt_consensus_best_source b USING (drug_id, role)
JOIN dt_consensus_ranked r USING (drug_id, role, target_id)
WHERE s.target_best_source = b.best_source
  AND r.source_priority = b.best_source
GROUP BY s.drug_id, s.role, s.target_id, s.target_best_source,
  s.independent_sources;
CREATE INDEX ON dt_consensus_candidates (drug_id, role, target_id);

CREATE TEMP TABLE dt_consensus_best_confidence ON COMMIT DROP AS
SELECT drug_id, role, MIN(target_best_confidence) AS best_confidence
FROM dt_consensus_candidates
GROUP BY drug_id, role;
CREATE INDEX ON dt_consensus_best_confidence (drug_id, role);

CREATE TEMP TABLE dt_consensus_best_support ON COMMIT DROP AS
SELECT c.drug_id, c.role, MAX(c.independent_sources) AS best_support
FROM dt_consensus_candidates c
JOIN dt_consensus_best_confidence b USING (drug_id, role)
WHERE c.target_best_confidence = b.best_confidence
GROUP BY c.drug_id, c.role;
CREATE INDEX ON dt_consensus_best_support (drug_id, role);

CREATE TEMP TABLE dt_consensus_resolved ON COMMIT DROP AS
SELECT c.drug_id, c.role, MIN(c.target_id) AS target_id
FROM dt_consensus_candidates c
JOIN dt_consensus_best_confidence b USING (drug_id, role)
JOIN dt_consensus_best_support s USING (drug_id, role)
WHERE c.target_best_confidence = b.best_confidence
  AND c.independent_sources = s.best_support
GROUP BY c.drug_id, c.role
HAVING COUNT(DISTINCT c.target_id) = 1;
CREATE UNIQUE INDEX ON dt_consensus_resolved (drug_id, role);

TRUNCATE preclin.drug_target_consensus_map;
INSERT INTO preclin.drug_target_consensus_map
  (drug_id, target_id, role, mechanism, source, confidence,
   target_symbol, target_family, target_tdl, refreshed_at)
SELECT DISTINCT ON (r.drug_id, r.role)
  r.drug_id, r.target_id, r.role, r.mechanism, r.source, r.confidence,
  t.symbol, t.family, t.tdl, now()
FROM dt_consensus_ranked r
JOIN dt_consensus_resolved x USING (drug_id, role, target_id)
JOIN dt_consensus_candidates c USING (drug_id, role, target_id)
JOIN public.targets t ON t.id = r.target_id
WHERE r.source_priority = c.target_best_source
  AND r.confidence_priority = c.target_best_confidence
ORDER BY r.drug_id, r.role, r.target_id, r.source, r.mechanism NULLS LAST;

CREATE OR REPLACE VIEW preclin.v_drug_target_consensus AS
SELECT drug_id, target_id, role, mechanism, source, confidence,
       target_symbol, target_family, target_tdl
FROM preclin.drug_target_consensus_map;

COMMENT ON TABLE preclin.drug_target_consensus_map IS
'Transactionally refreshed approval-independent unique target consensus after source priority, confidence, and independent-source corroboration; explicit FDA-approval mappings and unresolved strongest ties are excluded.';

CREATE OR REPLACE VIEW preclin.v_target_indication_strict_outcome AS
WITH ti_program_outcomes AS (
  SELECT
    dt.target_id, p.indication_id, p.program_id, p.sponsor_name,
    p.highest_phase, p.first_trial_date, p.last_trial_date,
    po.outcome, po.outcome_broad, po.approved_us, po.approved_ex_us,
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
), ti_rollup AS (
  SELECT
    target_id, indication_id,
    COUNT(*) AS n_programs,
    COUNT(DISTINCT sponsor_name) AS n_sponsors,
    MAX(highest_phase) AS max_phase_reached,
    MIN(first_trial_date) AS first_trial_date,
    MAX(last_trial_date) AS last_trial_date,
    BOOL_OR(approved_this_indication) AS strict_approved_this_ti,
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
