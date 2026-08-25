-- Use transparent LZ4 TOAST compression for large LLM audit values.
--
-- ALTER COLUMN ... SET COMPRESSION affects newly inserted or updated values;
-- it does not rewrite historical rows. Apply this before the cohort-wide
-- Nelson import so exact prompts remain queryable without paying their raw
-- multi-gigabyte storage cost.

BEGIN;

ALTER TABLE preclin.llm_run
  ALTER COLUMN system_prompt SET COMPRESSION lz4,
  ALTER COLUMN user_prompt SET COMPRESSION lz4,
  ALTER COLUMN raw_response SET COMPRESSION lz4,
  ALTER COLUMN parsed_output SET COMPRESSION lz4;

ALTER TABLE preclin.llm_run_source
  ALTER COLUMN excerpt_text SET COMPRESSION lz4;

ALTER TABLE preclin.evidence_score
  ALTER COLUMN value_json SET COMPRESSION lz4;

ALTER TABLE preclin.llm_run_evidence_score
  ALTER COLUMN fact_snapshot SET COMPRESSION lz4;

COMMIT;
