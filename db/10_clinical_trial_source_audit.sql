-- Immutable clinical-trial sources and exact LLM run inputs for verdict audits.

BEGIN;

CREATE TABLE IF NOT EXISTS preclin.source_document (
  source_document_id BIGSERIAL PRIMARY KEY,
  source_type        TEXT NOT NULL, -- registry_record | journal_abstract | press_release | other
  source_name        TEXT NOT NULL, -- clinicaltrials.gov | pubmed | ...
  external_id        TEXT NOT NULL, -- NCT id, PMID, URL, or provider identifier
  source_version     TEXT,
  source_url         TEXT,
  title              TEXT,
  abstract_text      TEXT,
  body_text          TEXT,
  raw_content        JSONB,
  raw_content_text   TEXT,
  media_type         TEXT NOT NULL,
  language           TEXT,
  content_sha256     TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  source_updated_at  TIMESTAMPTZ,
  retrieved_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  retrieval_method   TEXT NOT NULL,
  attribution        TEXT,
  rights_notice      TEXT,
  metadata           JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (source_name, external_id, content_sha256)
);
CREATE INDEX IF NOT EXISTS idx_source_document_external
  ON preclin.source_document (source_name, external_id, retrieved_at DESC, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_source_document_hash
  ON preclin.source_document (content_sha256);

COMMENT ON TABLE preclin.source_document IS
  'Immutable source snapshots. A changed upstream record creates a new row; identical content is idempotent.';
COMMENT ON COLUMN preclin.source_document.body_text IS
  'Deterministically normalized, human/model-readable text. raw_content or raw_content_text preserves the source payload.';

CREATE TABLE IF NOT EXISTS preclin.source_document_subject (
  subject_type       TEXT NOT NULL, -- trial | target | drug | target_indication | program
  subject_key        TEXT NOT NULL, -- NCT id, gene symbol, drug key, or stable composite key
  source_document_id BIGINT NOT NULL REFERENCES preclin.source_document(source_document_id) ON DELETE CASCADE,
  relationship       TEXT NOT NULL, -- registry_record | results_publication | background_publication | other
  discovered_from    TEXT,
  link_metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
  linked_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (subject_type, subject_key, source_document_id, relationship)
);
CREATE INDEX IF NOT EXISTS idx_source_document_subject_doc
  ON preclin.source_document_subject (source_document_id);
CREATE INDEX IF NOT EXISTS idx_source_document_subject_subject
  ON preclin.source_document_subject (subject_type, subject_key, relationship);

COMMENT ON TABLE preclin.source_document_subject IS
  'Many-to-many provenance links between trials/targets/drugs and immutable source snapshots.';

CREATE TABLE IF NOT EXISTS preclin.llm_run (
  run_id               UUID PRIMARY KEY,
  provider              TEXT,
  provider_request_id   TEXT,
  subject_type          TEXT NOT NULL,
  subject_key           TEXT NOT NULL,
  classifier_task       TEXT NOT NULL,
  classifier_model      TEXT NOT NULL,
  classifier_version    TEXT,
  system_prompt         TEXT COMPRESSION lz4,
  user_prompt           TEXT COMPRESSION lz4,
  user_prompt_compressed BYTEA,
  user_prompt_compression TEXT,
  user_prompt_uncompressed_bytes INTEGER,
  input_sha256          TEXT CHECK (input_sha256 IS NULL OR input_sha256 ~ '^[0-9a-f]{64}$'),
  raw_response          TEXT COMPRESSION lz4,
  output_sha256         TEXT CHECK (output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$'),
  parsed_output         JSONB COMPRESSION lz4,
  model_parameters      JSONB NOT NULL DEFAULT '{}'::jsonb,
  input_tokens          INTEGER,
  output_tokens         INTEGER,
  cost_usd              DOUBLE PRECISION,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (user_prompt_compression IS NULL OR user_prompt_compression = 'zlib')
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_llm_run_provider_request
  ON preclin.llm_run (provider, provider_request_id)
  WHERE provider_request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_llm_run_subject
  ON preclin.llm_run (subject_type, subject_key, classifier_task, created_at DESC);

COMMENT ON TABLE preclin.llm_run IS
  'Append-only record of one model call, including exact prompts, raw response, parsed output, parameters, and usage.';

CREATE TABLE IF NOT EXISTS preclin.llm_run_source (
  run_id               UUID NOT NULL REFERENCES preclin.llm_run(run_id) ON DELETE CASCADE,
  source_document_id   BIGINT NOT NULL REFERENCES preclin.source_document(source_document_id),
  relationship         TEXT NOT NULL DEFAULT 'model_input',
  ordinal              INTEGER NOT NULL DEFAULT 0,
  excerpt_text         TEXT COMPRESSION lz4,
  excerpt_sha256       TEXT CHECK (excerpt_sha256 IS NULL OR excerpt_sha256 ~ '^[0-9a-f]{64}$'),
  PRIMARY KEY (run_id, source_document_id, relationship, ordinal)
);

CREATE TABLE IF NOT EXISTS preclin.llm_run_evidence_score (
  run_id        UUID NOT NULL REFERENCES preclin.llm_run(run_id) ON DELETE CASCADE,
  evidence_id   BIGINT NOT NULL REFERENCES preclin.evidence_score(evidence_id) ON DELETE CASCADE,
  role          TEXT NOT NULL DEFAULT 'produced',
  fact_snapshot JSONB COMPRESSION lz4 NOT NULL,
  recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, evidence_id, role)
);
ALTER TABLE preclin.llm_run_evidence_score
  ADD COLUMN IF NOT EXISTS fact_snapshot JSONB,
  ADD COLUMN IF NOT EXISTS recorded_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- The first deployed revision linked runs directly to the mutable current-value
-- table. Backfill the only value still recoverable for those historical links;
-- future imports write the immutable run-produced value at insertion time.
UPDATE preclin.llm_run_evidence_score link
SET fact_snapshot = jsonb_build_object(
      'subject_type', es.subject_type,
      'subject_id', es.subject_id,
      'subject_id2', es.subject_id2,
      'dimension', es.dimension,
      'category', es.category,
      'value_numeric', es.value_numeric,
      'value_text', es.value_text,
      'value_boolean', es.value_boolean,
      'value_json', es.value_json,
      'source', es.source,
      'source_version', es.source_version,
      'confidence', es.confidence,
      'citation_pmids', es.citation_pmids,
      'extracted_by', es.extracted_by
    )
FROM preclin.evidence_score es
WHERE es.evidence_id = link.evidence_id
  AND link.fact_snapshot IS NULL;

ALTER TABLE preclin.llm_run_evidence_score
  ALTER COLUMN fact_snapshot SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_llm_run_evidence_score_evidence
  ON preclin.llm_run_evidence_score (evidence_id);

COMMENT ON TABLE preclin.llm_run_evidence_score IS
  'Connects one extraction call to each current evidence row while preserving the immutable value produced by that run in fact_snapshot.';

ALTER TABLE preclin.classification
  ADD COLUMN IF NOT EXISTS latest_run_id UUID REFERENCES preclin.llm_run(run_id) ON DELETE SET NULL;

-- Existing verdicts remain visible as legacy runs. Their exact inputs cannot be
-- reconstructed, which the audit view reports explicitly.
INSERT INTO preclin.llm_run (
  run_id, provider, subject_type, subject_key,
  classifier_task, classifier_model, classifier_version, parsed_output,
  cost_usd, created_at, model_parameters
)
SELECT md5('legacy-classification:' || c.classification_id::text)::uuid,
       'legacy_import', c.subject_type, c.subject_key,
       c.classifier_task, c.classifier_model, c.classifier_version,
       c.raw_output, c.cost_usd, c.extracted_at,
       '{"audit_status":"exact_input_unavailable"}'::jsonb
FROM preclin.classification c
WHERE c.latest_run_id IS NULL
ON CONFLICT (run_id) DO NOTHING;

UPDATE preclin.classification c
SET latest_run_id = md5('legacy-classification:' || c.classification_id::text)::uuid
WHERE c.latest_run_id IS NULL;

-- Group existing PubMed-derived evidence facts into the historical extraction
-- calls they most likely came from. This connects facts to a run without
-- pretending that the discarded exact prompt can be reconstructed.
WITH legacy_groups AS (
  SELECT es.subject_type,
         es.subject_id,
         es.subject_id2,
         es.source,
         es.source_version,
         COALESCE(t.symbol, d.normalized_name, es.subject_id::text) AS subject_key,
         COALESCE(es.extracted_by, es.source) AS classifier_model,
         MIN(es.extracted_at) AS created_at
  FROM preclin.evidence_score es
  LEFT JOIN public.targets t
    ON es.subject_type = 'target' AND t.id = es.subject_id
  LEFT JOIN preclin.drug d
    ON es.subject_type = 'drug' AND d.drug_id = es.subject_id
  WHERE es.source IN ('pubmed_haiku', 'pubmed_sonnet')
    AND NOT EXISTS (
      SELECT 1 FROM preclin.llm_run_evidence_score linked
      WHERE linked.evidence_id = es.evidence_id
    )
  GROUP BY es.subject_type, es.subject_id, es.subject_id2, es.source,
           es.source_version, t.symbol, d.normalized_name, es.extracted_by
)
INSERT INTO preclin.llm_run (
  run_id, provider, subject_type, subject_key, classifier_task,
  classifier_model, classifier_version, model_parameters, created_at
)
SELECT md5(
         'legacy-evidence:' || subject_type || ':' || subject_id::text || ':' ||
         COALESCE(subject_id2::text, '') || ':' || source || ':' ||
         COALESCE(source_version, '')
       )::uuid,
       'legacy_import', subject_type, subject_key,
       CASE source
         WHEN 'pubmed_haiku' THEN 'target_literature_score'
         ELSE 'drug_evidence_extract'
       END,
       classifier_model, source_version,
       '{"audit_status":"exact_input_unavailable"}'::jsonb, created_at
FROM legacy_groups
ON CONFLICT (run_id) DO NOTHING;

INSERT INTO preclin.llm_run_evidence_score
  (run_id, evidence_id, role, fact_snapshot)
SELECT md5(
         'legacy-evidence:' || es.subject_type || ':' || es.subject_id::text || ':' ||
         COALESCE(es.subject_id2::text, '') || ':' || es.source || ':' ||
         COALESCE(es.source_version, '')
       )::uuid,
       es.evidence_id, 'produced',
       jsonb_build_object(
         'subject_type', es.subject_type,
         'subject_id', es.subject_id,
         'subject_id2', es.subject_id2,
         'dimension', es.dimension,
         'category', es.category,
         'value_numeric', es.value_numeric,
         'value_text', es.value_text,
         'value_boolean', es.value_boolean,
         'value_json', es.value_json,
         'source', es.source,
         'source_version', es.source_version,
         'confidence', es.confidence,
         'citation_pmids', es.citation_pmids,
         'extracted_by', es.extracted_by
       )
FROM preclin.evidence_score es
WHERE es.source IN ('pubmed_haiku', 'pubmed_sonnet')
  AND NOT EXISTS (
    SELECT 1 FROM preclin.llm_run_evidence_score linked
    WHERE linked.evidence_id = es.evidence_id
  )
ON CONFLICT DO NOTHING;

CREATE OR REPLACE VIEW preclin.v_trial_source_latest AS
SELECT DISTINCT ON (tsd.subject_key, sd.source_name, sd.external_id, tsd.relationship)
       tsd.subject_key AS nct_id,
       tsd.relationship,
       sd.source_document_id,
       sd.source_type,
       sd.source_name,
       sd.external_id,
       sd.source_version,
       sd.source_url,
       sd.title,
       sd.abstract_text,
       CASE WHEN sd.source_name = 'clinicaltrials.gov'
            THEN sd.raw_content #>> '{protocolSection,statusModule,whyStopped}'
       END AS why_stopped_text,
       sd.body_text,
       sd.raw_content,
       sd.raw_content_text,
       sd.content_sha256,
       sd.retrieved_at,
       sd.last_seen_at,
       sd.rights_notice,
       tsd.link_metadata
FROM preclin.source_document_subject tsd
JOIN preclin.source_document sd USING (source_document_id)
WHERE tsd.subject_type = 'trial'
ORDER BY tsd.subject_key, sd.source_name, sd.external_id, tsd.relationship,
         sd.retrieved_at DESC, sd.source_document_id DESC;

COMMENT ON VIEW preclin.v_trial_source_latest IS
  'Latest immutable snapshot of every registry/publication source linked to each trial, including auditable text.';

CREATE OR REPLACE VIEW preclin.v_classification_audit AS
SELECT c.classification_id,
       c.subject_type,
       c.subject_key,
       c.classifier_task,
       c.category,
       c.confidence,
       c.rationale,
       c.classifier_model,
       c.classifier_version,
       c.extracted_at,
       r.run_id,
       r.provider,
       r.provider_request_id,
       r.system_prompt,
       r.user_prompt,
       r.raw_response,
       r.parsed_output,
       r.model_parameters,
       r.input_tokens,
       r.output_tokens,
       r.cost_usd,
       r.input_sha256,
       r.output_sha256,
       (r.system_prompt IS NOT NULL AND
        (r.user_prompt IS NOT NULL OR r.user_prompt_compressed IS NOT NULL))
         AS has_exact_input,
       COALESCE(src.sources, '[]'::jsonb) AS exact_input_sources,
       COALESCE(available.sources, '[]'::jsonb) AS available_trial_sources
FROM preclin.classification c
LEFT JOIN preclin.llm_run r ON r.run_id = c.latest_run_id
LEFT JOIN LATERAL (
  SELECT jsonb_agg(
           jsonb_build_object(
             'source_document_id', sd.source_document_id,
             'source_type', sd.source_type,
             'source_name', sd.source_name,
             'external_id', sd.external_id,
             'source_version', sd.source_version,
             'source_url', sd.source_url,
             'content_sha256', sd.content_sha256,
             'relationship', crs.relationship,
             'ordinal', crs.ordinal,
             'excerpt_text', crs.excerpt_text
           ) ORDER BY crs.ordinal, sd.source_document_id
         ) AS sources
  FROM preclin.llm_run_source crs
  JOIN preclin.source_document sd USING (source_document_id)
  WHERE crs.run_id = r.run_id
) src ON TRUE
LEFT JOIN LATERAL (
  SELECT jsonb_agg(
           jsonb_build_object(
             'source_document_id', sd.source_document_id,
             'source_type', sd.source_type,
             'source_name', sd.source_name,
             'external_id', sd.external_id,
             'relationship', sd.relationship,
             'title', sd.title,
             'why_stopped_text', sd.why_stopped_text,
             'abstract_text', sd.abstract_text,
             'source_url', sd.source_url,
             'content_sha256', sd.content_sha256,
             'retrieved_at', sd.retrieved_at
           ) ORDER BY sd.relationship, sd.source_name, sd.external_id
         ) AS sources
  FROM preclin.v_trial_source_latest sd
  WHERE c.subject_type = 'trial' AND sd.nct_id = c.subject_key
) available ON TRUE;

COMMENT ON VIEW preclin.v_classification_audit IS
  'One-row audit surface for each current verdict: exact model call plus immutable linked sources.';

CREATE OR REPLACE VIEW preclin.v_evidence_score_audit AS
SELECT es.evidence_id,
       es.subject_type,
       es.subject_id,
       es.subject_id2,
       es.dimension,
       es.category,
       es.value_numeric,
       es.value_text,
       es.value_boolean,
       es.value_json,
       es.source,
       es.source_version,
       es.confidence,
       es.citation_pmids,
       es.extracted_at,
       es.extracted_by,
       link.role AS run_role,
       r.run_id,
       r.provider,
       r.provider_request_id,
       r.classifier_task,
       r.classifier_model,
       r.classifier_version,
       r.system_prompt,
       r.user_prompt,
       r.raw_response,
       r.parsed_output,
       r.model_parameters,
       r.input_tokens,
       r.output_tokens,
       r.cost_usd,
       r.input_sha256,
       r.output_sha256,
       (r.system_prompt IS NOT NULL AND
        (r.user_prompt IS NOT NULL OR r.user_prompt_compressed IS NOT NULL))
         AS has_exact_input,
       COALESCE(src.sources, '[]'::jsonb) AS exact_input_sources,
       link.fact_snapshot AS run_fact_snapshot,
       link.recorded_at AS run_fact_recorded_at
FROM preclin.evidence_score es
LEFT JOIN preclin.llm_run_evidence_score link USING (evidence_id)
LEFT JOIN preclin.llm_run r USING (run_id)
LEFT JOIN LATERAL (
  SELECT jsonb_agg(
           jsonb_build_object(
             'source_document_id', sd.source_document_id,
             'source_type', sd.source_type,
             'source_name', sd.source_name,
             'external_id', sd.external_id,
             'source_version', sd.source_version,
             'title', sd.title,
             'abstract_text', sd.abstract_text,
             'source_url', sd.source_url,
             'content_sha256', sd.content_sha256,
             'relationship', lrs.relationship,
             'ordinal', lrs.ordinal,
             'excerpt_text', lrs.excerpt_text,
             'excerpt_sha256', lrs.excerpt_sha256
           ) ORDER BY lrs.ordinal, sd.source_document_id
         ) AS sources
  FROM preclin.llm_run_source lrs
  JOIN preclin.source_document sd USING (source_document_id)
  WHERE lrs.run_id = r.run_id
) src ON TRUE;

COMMENT ON VIEW preclin.v_evidence_score_audit IS
  'Current evidence rows joined to exact extraction runs and immutable run_fact_snapshot values; unqualified value columns are the current projection and may differ after a later run.';

COMMIT;
