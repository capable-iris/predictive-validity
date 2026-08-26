-- Normalize the ChEMBL target-level approval history used by the
-- approval–evidence event study. This is intentionally separate from
-- preclin.approval, whose rows are exact drug–indication regulatory outcomes.

BEGIN;

CREATE TABLE IF NOT EXISTS preclin.chembl_target_approval_release (
  chembl_db_version  TEXT PRIMARY KEY,
  release_date       DATE NOT NULL,
  source             TEXT NOT NULL,
  source_url         TEXT NOT NULL,
  mapping_policy     TEXT NOT NULL,
  source_audit       JSONB NOT NULL,
  imported_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS preclin.chembl_target_mapping (
  chembl_db_version  TEXT NOT NULL REFERENCES
    preclin.chembl_target_approval_release(chembl_db_version)
    ON DELETE CASCADE,
  target_id          INTEGER NOT NULL REFERENCES public.targets(id),
  target_symbol      TEXT NOT NULL,
  target_chembl_id   TEXT NOT NULL,
  PRIMARY KEY (chembl_db_version, target_id, target_chembl_id)
);

CREATE INDEX IF NOT EXISTS idx_chembl_target_mapping_target
ON preclin.chembl_target_mapping (target_id);

CREATE TABLE IF NOT EXISTS preclin.chembl_target_approval_event (
  event_id            BIGSERIAL PRIMARY KEY,
  chembl_db_version   TEXT NOT NULL REFERENCES
    preclin.chembl_target_approval_release(chembl_db_version)
    ON DELETE CASCADE,
  target_id           INTEGER NOT NULL REFERENCES public.targets(id),
  target_chembl_id    TEXT NOT NULL,
  molecule_chembl_id  TEXT NOT NULL,
  molecule_name       TEXT,
  first_approval_year INTEGER NOT NULL CHECK (first_approval_year BETWEEN 1900 AND 2100),
  action_type         TEXT,
  mechanism_of_action TEXT,
  CONSTRAINT fk_chembl_target_approval_event_mapping FOREIGN KEY
    (chembl_db_version, target_id, target_chembl_id)
    REFERENCES preclin.chembl_target_mapping
      (chembl_db_version, target_id, target_chembl_id)
    ON DELETE CASCADE
);

-- CREATE TABLE IF NOT EXISTS does not retrofit constraints on an established
-- database, so add the relationship when upgrading a partially applied v25.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'fk_chembl_target_approval_event_mapping'
      AND conrelid = 'preclin.chembl_target_approval_event'::regclass
  ) THEN
    ALTER TABLE preclin.chembl_target_approval_event
      ADD CONSTRAINT fk_chembl_target_approval_event_mapping FOREIGN KEY
        (chembl_db_version, target_id, target_chembl_id)
        REFERENCES preclin.chembl_target_mapping
          (chembl_db_version, target_id, target_chembl_id)
        ON DELETE CASCADE;
  END IF;
END
$$;

-- ChEMBL can return byte-identical mechanism rows more than once. Preserve
-- distinct actions/descriptions while making repeated imports idempotent.
CREATE UNIQUE INDEX IF NOT EXISTS uq_chembl_target_approval_event
ON preclin.chembl_target_approval_event (
  chembl_db_version,
  target_id,
  target_chembl_id,
  molecule_chembl_id,
  COALESCE(action_type, ''),
  COALESCE(mechanism_of_action, '')
);

CREATE INDEX IF NOT EXISTS idx_chembl_target_approval_event_target
ON preclin.chembl_target_approval_event (target_id, first_approval_year);

CREATE OR REPLACE VIEW preclin.v_chembl_target_first_approval AS
WITH latest_release AS (
  SELECT chembl_db_version
  FROM preclin.chembl_target_approval_release
  ORDER BY release_date DESC, imported_at DESC, chembl_db_version DESC
  LIMIT 1
),
target_summary AS (
  SELECT
    m.chembl_db_version,
    m.target_id,
    m.target_symbol,
    array_agg(DISTINCT m.target_chembl_id ORDER BY m.target_chembl_id)
      AS target_chembl_ids,
    min(e.first_approval_year) AS first_approval_year,
    count(DISTINCT e.event_id) AS supporting_mechanism_count
  FROM preclin.chembl_target_mapping m
  JOIN latest_release r USING (chembl_db_version)
  LEFT JOIN preclin.chembl_target_approval_event e
    ON e.chembl_db_version = m.chembl_db_version
   AND e.target_id = m.target_id
   AND e.target_chembl_id = m.target_chembl_id
  GROUP BY m.chembl_db_version, m.target_id, m.target_symbol
)
SELECT
  s.*,
  r.release_date,
  r.source,
  r.source_url,
  r.mapping_policy,
  r.source_audit,
  r.imported_at
FROM target_summary s
JOIN preclin.chembl_target_approval_release r USING (chembl_db_version);

COMMENT ON TABLE preclin.chembl_target_approval_release IS
  'Versioned provenance for ChEMBL target–molecule first-approval imports.';
COMMENT ON TABLE preclin.chembl_target_mapping IS
  'Exact local gene-symbol mappings to ChEMBL human single-protein targets; includes mapped targets with no approval event.';
COMMENT ON TABLE preclin.chembl_target_approval_event IS
  'Molecule-level ChEMBL first-approval years for direct approved mechanisms; not an exact target–indication regulatory outcome.';
COMMENT ON VIEW preclin.v_chembl_target_first_approval IS
  'Earliest molecule first-approval year for every target mapped in the latest imported ChEMBL release; NULL means mapped with no qualifying event.';

COMMIT;
