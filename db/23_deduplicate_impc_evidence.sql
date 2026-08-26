-- Collapse repeated imports of the same versioned target-level IMPC summary.
-- The generic evidence_score UNIQUE constraint treats NULL subject_id2 values
-- as distinct, so repeated target-level imports were not actually idempotent.

BEGIN;

WITH ranked AS (
  SELECT evidence_id,
         row_number() OVER (
           PARTITION BY subject_id, source_version
           ORDER BY evidence_id
         ) AS duplicate_number
  FROM preclin.evidence_score
  WHERE subject_type = 'target'
    AND subject_id2 IS NULL
    AND dimension = 'impc_n_phenotypes'
    AND source = 'impc'
)
DELETE FROM preclin.evidence_score es
USING ranked r
WHERE es.evidence_id = r.evidence_id
  AND r.duplicate_number > 1;

-- Prevent another target-level IMPC import from recreating duplicates while
-- still allowing one row per target for each source release.
CREATE UNIQUE INDEX IF NOT EXISTS uq_es_impc_target_release
ON preclin.evidence_score (subject_id, source_version)
WHERE subject_type = 'target'
  AND subject_id2 IS NULL
  AND dimension = 'impc_n_phenotypes'
  AND source = 'impc';

COMMIT;
