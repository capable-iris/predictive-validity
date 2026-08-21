-- Add versioned GWAS study publication dates for temporal Nelson dossiers.
-- Idempotent migration for established databases; the same definitions live
-- in 01_schema.sql for fresh installations.

BEGIN;

CREATE TABLE IF NOT EXISTS preclin.evidence_source_release (
  source_release_id BIGSERIAL PRIMARY KEY,
  source_name       TEXT NOT NULL,
  source_version    TEXT NOT NULL,
  source_url        TEXT NOT NULL,
  content_sha256    TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  retrieved_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (source_name, source_version, content_sha256)
);

CREATE TABLE IF NOT EXISTS preclin.gwas_study_date (
  source_release_id       BIGINT NOT NULL REFERENCES preclin.evidence_source_release(source_release_id),
  study_accession         TEXT NOT NULL,
  study_pmid              TEXT NOT NULL,
  evidence_available_date DATE NOT NULL,
  catalog_added_date      DATE,
  date_basis              TEXT NOT NULL DEFAULT 'gwas_catalog_publication_date',
  date_precision          TEXT NOT NULL DEFAULT 'day',
  source_url              TEXT NOT NULL,
  metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (source_release_id, study_accession),
  CHECK (date_precision IN ('day', 'month', 'year'))
);

CREATE INDEX IF NOT EXISTS idx_gwas_study_date_accession
  ON preclin.gwas_study_date (study_accession, source_release_id DESC);
CREATE INDEX IF NOT EXISTS idx_gwas_study_date_pmid
  ON preclin.gwas_study_date (study_pmid);

CREATE OR REPLACE VIEW preclin.v_gwas_study_date_latest AS
SELECT DISTINCT ON (gsd.study_accession)
       gsd.study_accession,
       gsd.study_pmid,
       gsd.evidence_available_date,
       gsd.catalog_added_date,
       gsd.date_basis,
       gsd.date_precision,
       gsd.source_url,
       esr.source_version,
       esr.content_sha256 AS source_content_sha256,
       esr.retrieved_at AS source_retrieved_at
FROM preclin.gwas_study_date gsd
JOIN preclin.evidence_source_release esr USING (source_release_id)
ORDER BY gsd.study_accession, esr.retrieved_at DESC, source_release_id DESC;

COMMENT ON TABLE preclin.gwas_study_date IS
'Versioned publication/availability dates for GWAS Catalog studies. Association rows inherit dates by study_accession.';
COMMENT ON COLUMN preclin.gwas_study_date.evidence_available_date IS
'GWAS Catalog DATE: online publication date when available, otherwise print publication date.';

COMMIT;
