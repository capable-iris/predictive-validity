-- preclin.* — preclinical evidence + drug-outcome analysis layer
-- Design: DB_SCHEMA_DESIGN.md
-- Principles: single source of truth, long-form facts, wide-form views,
--             reuse public.* where authoritative, extend where sparse.

BEGIN;

CREATE SCHEMA IF NOT EXISTS preclin;

-- ============================================================
-- ENTITY: canonical drug identity
-- ============================================================
CREATE TABLE IF NOT EXISTS preclin.drug (
  drug_id            SERIAL PRIMARY KEY,
  normalized_name    TEXT NOT NULL UNIQUE,       -- lowercased, punctuation stripped
  display_name       TEXT NOT NULL,              -- as first seen (canonical spelling)
  therapy_id         INTEGER REFERENCES public.therapies(id),  -- when overlap
  chembl_id          TEXT,
  drugbank_id        TEXT,
  modality           TEXT,                       -- small_molecule, mab, adc, car_t, aso, sirna, gene_therapy, vaccine, cell_therapy, other
  modality_subtype   TEXT,
  mechanism          TEXT,                       -- agonist, antagonist, inhibitor, degrader, activator, cytotoxic, other
  route_of_admin     TEXT,
  is_placebo         BOOLEAN NOT NULL DEFAULT FALSE,
  is_combination     BOOLEAN NOT NULL DEFAULT FALSE,
  is_biosimilar      BOOLEAN NOT NULL DEFAULT FALSE,
  synonyms           TEXT[],
  resolved_via       TEXT,                       -- public_therapy | chembl_bulk | llm_haiku | llm_sonnet_verified | llm_sonnet | manual | unresolved
  resolved_confidence TEXT,                      -- high | medium | low | n/a
  resolved_at        TIMESTAMPTZ,
  notes              TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_drug_therapy ON preclin.drug(therapy_id);
CREATE INDEX IF NOT EXISTS idx_drug_chembl ON preclin.drug(chembl_id);
CREATE INDEX IF NOT EXISTS idx_drug_resolved_via ON preclin.drug(resolved_via);

COMMENT ON TABLE preclin.drug IS 'Canonical drug identity. Superset of public.therapies (~11k) with all CT.gov trial-only drugs added.';
COMMENT ON COLUMN preclin.drug.resolved_via IS 'Provenance of drug→target mapping. Critical for selection-bias analysis.';

-- ============================================================
-- ENTITY: drug → target(s) junction (multi-target)
-- ============================================================
CREATE TABLE IF NOT EXISTS preclin.drug_target (
  id             BIGSERIAL PRIMARY KEY,
  drug_id        INTEGER NOT NULL REFERENCES preclin.drug(drug_id) ON DELETE CASCADE,
  target_id      INTEGER NOT NULL REFERENCES public.targets(id),
  role           TEXT NOT NULL,        -- primary | secondary | off_target | component_of_combo
  mechanism      TEXT,                 -- agonist | antagonist | inhibitor | degrader | activator | cytotoxic | other
  source         TEXT NOT NULL,        -- chembl_bulk | therapy_targets_public | llm_haiku | llm_sonnet_verified | llm_sonnet | fda_approval | manual
  source_version TEXT,
  confidence     TEXT,                 -- high | medium | low
  citation_pmid  TEXT,
  citation_doi   TEXT,
  rationale      TEXT,
  extracted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (drug_id, target_id, role, source, source_version)
);
CREATE INDEX IF NOT EXISTS idx_dt_drug ON preclin.drug_target(drug_id);
CREATE INDEX IF NOT EXISTS idx_dt_target ON preclin.drug_target(target_id);
CREATE INDEX IF NOT EXISTS idx_dt_source ON preclin.drug_target(source);

COMMENT ON TABLE preclin.drug_target IS 'Drug→target linkage. Multi-source (ChEMBL + LLM + FDA). Views resolve to highest-confidence per drug.';

-- ============================================================
-- ENTITY: canonical indications
-- ============================================================
CREATE TABLE IF NOT EXISTS preclin.indication (
  indication_id      SERIAL PRIMARY KEY,
  normalized_name    TEXT NOT NULL UNIQUE,       -- lowercased, punctuation stripped
  display_name       TEXT NOT NULL,
  disease_id         INTEGER REFERENCES public.diseases(id),  -- when a curated match exists
  mondo_id           TEXT,
  efo_id             TEXT,
  therapeutic_area   TEXT,                       -- oncology | neuro | autoimmune | rare | cv | metabolic | infectious | other
  ct_gov_conditions  TEXT[],                     -- variant strings observed in CT.gov
  notes              TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ind_disease ON preclin.indication(disease_id);
CREATE INDEX IF NOT EXISTS idx_ind_area ON preclin.indication(therapeutic_area);

COMMENT ON TABLE preclin.indication IS 'Canonical indications derived from CT.gov conditions + FDA labels. Superset of public.diseases (~167 curated).';

-- ============================================================
-- ENTITY: approvals (extends public.approvals)
-- ============================================================
CREATE TABLE IF NOT EXISTS preclin.approval (
  approval_id        SERIAL PRIMARY KEY,
  drug_id            INTEGER NOT NULL REFERENCES preclin.drug(drug_id),
  indication_id      INTEGER REFERENCES preclin.indication(indication_id),
  agency             TEXT NOT NULL,              -- FDA_CDER | FDA_CBER | EMA | PMDA | NMPA | Health_Canada | other
  region             TEXT NOT NULL,              -- US | EU | JP | CN | CA | other
  approval_date      DATE,
  approval_year      INTEGER,
  brand_name         TEXT,
  sponsor_id         INTEGER REFERENCES public.sponsors(id),
  sponsor_name       TEXT,                       -- as stated on label
  nelson_tier        TEXT,                       -- T0 | T1 | T2 | T3 | T4
  nelson_evidence_url TEXT,
  first_in_class     BOOLEAN,
  orphan             BOOLEAN,
  breakthrough       BOOLEAN,
  accelerated        BOOLEAN,
  fast_track         BOOLEAN,
  priority_review    BOOLEAN,
  application_number TEXT,
  source_url         TEXT,
  public_approval_id INTEGER REFERENCES public.approvals(id),  -- when overlap
  notes              TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_appr_drug ON preclin.approval(drug_id);
CREATE INDEX IF NOT EXISTS idx_appr_indication ON preclin.approval(indication_id);
CREATE INDEX IF NOT EXISTS idx_appr_agency ON preclin.approval(agency);
CREATE INDEX IF NOT EXISTS idx_appr_region ON preclin.approval(region);

COMMENT ON TABLE preclin.approval IS 'All drug approvals (US + ex-US). Extends public.approvals (only 166 rows).';
COMMENT ON COLUMN preclin.approval.region IS 'US = FDA-approved. Others count separately for approved-anywhere analysis.';

-- ============================================================
-- ENTITY: program (drug × indication × sponsor) — the analytical unit
-- ============================================================
CREATE TABLE IF NOT EXISTS preclin.program (
  program_id            SERIAL PRIMARY KEY,
  drug_id               INTEGER NOT NULL REFERENCES preclin.drug(drug_id),
  indication_id         INTEGER NOT NULL REFERENCES preclin.indication(indication_id),
  sponsor_id            INTEGER REFERENCES public.sponsors(id),
  sponsor_name          TEXT,                    -- as first observed (for unresolved sponsor_id)
  first_trial_date      DATE,
  last_trial_date       DATE,
  highest_phase         INTEGER,                 -- 0-4
  n_trials              INTEGER NOT NULL DEFAULT 0,
  n_trials_ph1          INTEGER NOT NULL DEFAULT 0,
  n_trials_ph2          INTEGER NOT NULL DEFAULT 0,
  n_trials_ph3          INTEGER NOT NULL DEFAULT 0,
  n_trials_ph4          INTEGER NOT NULL DEFAULT 0,
  n_terminated          INTEGER NOT NULL DEFAULT 0,
  n_withdrawn           INTEGER NOT NULL DEFAULT 0,
  n_suspended           INTEGER NOT NULL DEFAULT 0,
  n_completed           INTEGER NOT NULL DEFAULT 0,
  n_active              INTEGER NOT NULL DEFAULT 0,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (drug_id, indication_id, sponsor_id, sponsor_name)
);
CREATE INDEX IF NOT EXISTS idx_prog_drug ON preclin.program(drug_id);
CREATE INDEX IF NOT EXISTS idx_prog_indication ON preclin.program(indication_id);
CREATE INDEX IF NOT EXISTS idx_prog_sponsor ON preclin.program(sponsor_id);
CREATE INDEX IF NOT EXISTS idx_prog_phase ON preclin.program(highest_phase);

COMMENT ON TABLE preclin.program IS 'One row per (drug × indication × sponsor). The unit at which "approved" or "failed for efficacy" is a valid statement.';

-- ============================================================
-- JUNCTION: program × trial
-- ============================================================
CREATE TABLE IF NOT EXISTS preclin.program_trial (
  program_id  INTEGER NOT NULL REFERENCES preclin.program(program_id) ON DELETE CASCADE,
  nct_id      TEXT NOT NULL,
  phase       INTEGER,
  status      TEXT,
  PRIMARY KEY (program_id, nct_id)
);
CREATE INDEX IF NOT EXISTS idx_pt_nct ON preclin.program_trial(nct_id);

COMMENT ON TABLE preclin.program_trial IS 'program × trial junction. nct_id references public.trials but we don''t enforce FK to allow trials outside our subset.';

-- ============================================================
-- COMPUTED: program outcome rollup
-- ============================================================
CREATE TABLE IF NOT EXISTS preclin.program_outcome (
  program_id           INTEGER PRIMARY KEY REFERENCES preclin.program(program_id) ON DELETE CASCADE,
  outcome              TEXT NOT NULL,      -- approved | efficacy_fail | safety_fail | commercial_fail | enrollment_fail | other_fail | phase_complete_no_approval | phase1_complete_no_advance | in_development | planned_termination | unknown
  outcome_broad        TEXT NOT NULL,      -- approved | efficacy_fail | safety_fail | commercial_fail | enrollment_fail | presumptive_efficacy_fail_ph3 | presumptive_fail_ph2 | unclassified_termination | phase1_only | planned_termination | in_dev
  outcome_confidence   TEXT NOT NULL,      -- high | medium | low
  approved_us          BOOLEAN NOT NULL DEFAULT FALSE,
  approved_ex_us       BOOLEAN NOT NULL DEFAULT FALSE,
  first_approval_id    INTEGER REFERENCES preclin.approval(approval_id),
  failure_reasons      JSONB,              -- {"efficacy": 2, "safety": 0, ...}
  computed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  method               TEXT NOT NULL,      -- 'rollup_v1' | 'sonnet_verified' | 'manual'
  notes                TEXT
);
CREATE INDEX IF NOT EXISTS idx_po_outcome ON preclin.program_outcome(outcome);
CREATE INDEX IF NOT EXISTS idx_po_broad ON preclin.program_outcome(outcome_broad);
CREATE INDEX IF NOT EXISTS idx_po_approved ON preclin.program_outcome(approved_us, approved_ex_us);

COMMENT ON COLUMN preclin.program_outcome.approved_us IS 'FDA-approved (CDER or CBER)';
COMMENT ON COLUMN preclin.program_outcome.approved_ex_us IS 'Approved by ≥1 non-US agency (EMA / PMDA / NMPA / etc.)';

-- ============================================================
-- FACT: evidence scores (LONG form)
-- ============================================================
CREATE TABLE IF NOT EXISTS preclin.evidence_score (
  evidence_id     BIGSERIAL PRIMARY KEY,
  subject_type    TEXT NOT NULL,        -- target | target_indication | drug | program
  subject_id      INTEGER NOT NULL,     -- refers to targets.id / indication_id / drug_id / program_id
  subject_id2     INTEGER,              -- for target_indication: indication_id (subject_id = target_id)
  dimension       TEXT NOT NULL,        -- e.g. 'line_c_lit', 'depmap_pan_essential', 'mendelian_n', 'nelson_tier'
  category        TEXT NOT NULL,        -- A_genetics | B_mechanistic | C_cell | D_animal | E_pd | F_clinical | G_pharmacology | H_safety | I_landscape
  value_numeric   DOUBLE PRECISION,
  value_text      TEXT,
  value_boolean   BOOLEAN,
  value_json      JSONB COMPRESSION lz4, -- for compound/nested values
  source          TEXT NOT NULL,        -- pubmed_haiku | pubmed_sonnet | depmap | gnomad | clingen | mendelian | gwas | impc | ot_composite | ot_animal_model | chembl | fda_approval | manual
  source_version  TEXT,                 -- e.g. '2026-01' | 'v1.2' | pubmed cutoff date
  confidence      TEXT,                 -- high | medium | low
  citation_pmids  TEXT[],
  citation_details JSONB,
  extracted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  extracted_by    TEXT,                 -- claude-haiku | claude-sonnet | manual | script:xxx
  notes           TEXT,
  UNIQUE (subject_type, subject_id, subject_id2, dimension, source, source_version)
);
CREATE INDEX IF NOT EXISTS idx_es_subject ON preclin.evidence_score(subject_type, subject_id);
CREATE INDEX IF NOT EXISTS idx_es_subject_indication ON preclin.evidence_score(subject_type, subject_id, subject_id2);
CREATE INDEX IF NOT EXISTS idx_es_dimension ON preclin.evidence_score(dimension);
CREATE INDEX IF NOT EXISTS idx_es_category ON preclin.evidence_score(category);
CREATE INDEX IF NOT EXISTS idx_es_source ON preclin.evidence_score(source);

COMMENT ON TABLE preclin.evidence_score IS 'LONG-form fact table. Every evidence claim = one row. Add new evidence type = new dimension string, no ALTER TABLE.';
COMMENT ON COLUMN preclin.evidence_score.subject_type IS 'target: gene-level | target_indication: gene × disease | drug: drug-level | program: (drug × ind × sponsor)';
COMMENT ON COLUMN preclin.evidence_score.subject_id2 IS 'For target_indication rows, subject_id = target_id, subject_id2 = indication_id.';

CREATE TABLE IF NOT EXISTS preclin.hpo_phenotypic_abnormality_term (
  hpo_id            TEXT PRIMARY KEY,
  ontology_release  TEXT NOT NULL,
  ontology_sha256   TEXT NOT NULL,
  branch_root       TEXT NOT NULL,
  loaded_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE preclin.hpo_phenotypic_abnormality_term IS
'Positive allowlist of active descendants of HP:0000118 from a pinned official HPO release.';

-- ============================================================
-- FACT: LLM classifications
-- ============================================================
CREATE TABLE IF NOT EXISTS preclin.classification (
  classification_id  BIGSERIAL PRIMARY KEY,
  subject_type       TEXT NOT NULL,     -- trial | program | drug | target_indication
  subject_key        TEXT NOT NULL,     -- nct_id | program_id-as-text | drug_id-as-text | (target_id, indication_id)-as-text
  classifier_task    TEXT NOT NULL,     -- why_stopped | silent_kill_verify | target_resolution | tier_assignment | outcome_promotion
  category           TEXT NOT NULL,     -- e.g. efficacy | safety | commercial_strategic | (or gene symbol for target_resolution)
  confidence         TEXT,              -- high | medium | low
  rationale          TEXT,
  citation_pmids     TEXT[],
  citation_ncts      TEXT[],
  classifier_model   TEXT NOT NULL,     -- claude-haiku | claude-sonnet | regex_v1
  classifier_version TEXT,              -- date or version string
  cost_usd           DOUBLE PRECISION,
  extracted_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_output         JSONB,             -- store full LLM response for audit
  UNIQUE (subject_type, subject_key, classifier_task, classifier_model, classifier_version)
);
CREATE INDEX IF NOT EXISTS idx_cls_subject ON preclin.classification(subject_type, subject_key);
CREATE INDEX IF NOT EXISTS idx_cls_task ON preclin.classification(classifier_task);
CREATE INDEX IF NOT EXISTS idx_cls_category ON preclin.classification(category);
CREATE INDEX IF NOT EXISTS idx_cls_model ON preclin.classification(classifier_model);

COMMENT ON TABLE preclin.classification IS 'LLM outputs: why_stopped, silent_kill_verify, target_resolution. Multiple classifiers coexist per subject; disagreement is directly queryable.';

-- ============================================================
-- PROVENANCE: immutable sources + generic LLM runs
-- (also created/backfilled idempotently by migration 10)
-- ============================================================
CREATE TABLE IF NOT EXISTS preclin.source_document (
  source_document_id BIGSERIAL PRIMARY KEY,
  source_type        TEXT NOT NULL,
  source_name        TEXT NOT NULL,
  external_id        TEXT NOT NULL,
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

CREATE TABLE IF NOT EXISTS preclin.source_document_subject (
  subject_type       TEXT NOT NULL,
  subject_key        TEXT NOT NULL,
  source_document_id BIGINT NOT NULL REFERENCES preclin.source_document(source_document_id) ON DELETE CASCADE,
  relationship       TEXT NOT NULL,
  discovered_from    TEXT,
  link_metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
  linked_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (subject_type, subject_key, source_document_id, relationship)
);
CREATE INDEX IF NOT EXISTS idx_source_document_subject_doc
  ON preclin.source_document_subject (source_document_id);
CREATE INDEX IF NOT EXISTS idx_source_document_subject_subject
  ON preclin.source_document_subject (subject_type, subject_key, relationship);

-- ============================================================
-- PROVENANCE: evidence availability dates
-- ============================================================
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
  input_sha256          TEXT CHECK (input_sha256 IS NULL OR input_sha256 ~ '^[0-9a-f]{64}$'),
  raw_response          TEXT COMPRESSION lz4,
  output_sha256         TEXT CHECK (output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$'),
  parsed_output         JSONB COMPRESSION lz4,
  model_parameters      JSONB NOT NULL DEFAULT '{}'::jsonb,
  input_tokens          INTEGER,
  output_tokens         INTEGER,
  cost_usd              DOUBLE PRECISION,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_llm_run_provider_request
  ON preclin.llm_run (provider, provider_request_id)
  WHERE provider_request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_llm_run_subject
  ON preclin.llm_run (subject_type, subject_key, classifier_task, created_at DESC);

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
CREATE INDEX IF NOT EXISTS idx_llm_run_evidence_score_evidence
  ON preclin.llm_run_evidence_score (evidence_id);

ALTER TABLE preclin.classification
  ADD COLUMN IF NOT EXISTS latest_run_id UUID REFERENCES preclin.llm_run(run_id) ON DELETE SET NULL;

-- ============================================================
-- METADATA: dimension registry (documentation, not a FK target)
-- ============================================================
CREATE TABLE IF NOT EXISTS preclin.evidence_dimension (
  dimension       TEXT PRIMARY KEY,
  category        TEXT NOT NULL,
  subject_type    TEXT NOT NULL,        -- what subject_type this dimension applies to
  data_type       TEXT NOT NULL,        -- numeric_0_3 | numeric_float | count | boolean | text | categorical
  description     TEXT NOT NULL,
  source_primary  TEXT,
  tier_definition JSONB,                -- e.g. {"0": "none", "1": "PK only", "2": "solid efficacy", "3": "multi-species replicated"}
  added_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE preclin.evidence_dimension IS 'Catalog of every evidence dimension we track. Adding a dimension: insert here + start writing to evidence_score.';

-- ============================================================
-- PROVENANCE: ingest audit log
-- ============================================================
CREATE TABLE IF NOT EXISTS preclin.ingest_log (
  ingest_id    BIGSERIAL PRIMARY KEY,
  source_file  TEXT NOT NULL,
  target_table TEXT NOT NULL,
  rows_read    INTEGER,
  rows_inserted INTEGER,
  rows_skipped INTEGER,
  rows_updated INTEGER,
  started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at  TIMESTAMPTZ,
  status       TEXT,          -- ok | partial | failed
  notes        TEXT
);

COMMENT ON TABLE preclin.ingest_log IS 'Provenance: every JSONL/CSV → DB ingest logged for reproducibility + debugging.';

COMMIT;
