-- Preserve exact large prompts as client-compressed bytes.
--
-- PostgreSQL TOAST compression reduces disk use but not client-to-server
-- transfer. Cohort-wide Nelson prompts are therefore zlib-compressed before
-- COPY, while input_sha256 continues to identify the exact uncompressed model
-- input. Legacy and smaller runs may continue to use user_prompt text.

BEGIN;

ALTER TABLE preclin.llm_run
  ADD COLUMN IF NOT EXISTS user_prompt_compressed BYTEA,
  ADD COLUMN IF NOT EXISTS user_prompt_compression TEXT,
  ADD COLUMN IF NOT EXISTS user_prompt_uncompressed_bytes INTEGER;

ALTER TABLE preclin.llm_run
  DROP CONSTRAINT IF EXISTS llm_run_user_prompt_compression_check,
  ADD CONSTRAINT llm_run_user_prompt_compression_check
    CHECK (user_prompt_compression IS NULL OR user_prompt_compression = 'zlib');

COMMENT ON COLUMN preclin.llm_run.user_prompt_compressed IS
'Exact UTF-8 user prompt compressed client-side; decode according to user_prompt_compression.';
COMMENT ON COLUMN preclin.llm_run.user_prompt_uncompressed_bytes IS
'UTF-8 byte length before compression, for integrity and storage auditing.';

DO $$
DECLARE
  audit_view text;
  audit_name text;
BEGIN
  FOREACH audit_name IN ARRAY ARRAY[
    'v_classification_audit',
    'v_evidence_score_audit'
  ]
  LOOP
    SELECT pg_get_viewdef(format('preclin.%I', audit_name)::regclass, true)
      INTO audit_view;
    IF position('user_prompt_compressed' IN audit_view) = 0 THEN
      audit_view := replace(
        audit_view,
        'r.user_prompt IS NOT NULL',
        '(r.user_prompt IS NOT NULL OR r.user_prompt_compressed IS NOT NULL)'
      );
      EXECUTE format(
        'CREATE OR REPLACE VIEW preclin.%I AS %s', audit_name, audit_view
      );
    END IF;
  END LOOP;
END
$$;

COMMIT;
