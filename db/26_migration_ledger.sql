-- Append-only, checksum-aware history for numbered database migrations.
-- Recurring source ingests remain in preclin.ingest_log or their dedicated
-- release tables; benchmark executions remain in preclin.benchmark_run.

BEGIN;

CREATE TABLE IF NOT EXISTS preclin.schema_migration (
  migration_number INTEGER PRIMARY KEY CHECK (migration_number > 0),
  migration_name   TEXT NOT NULL,
  filename         TEXT NOT NULL UNIQUE,
  file_sha256      TEXT NOT NULL CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
  migration_kind   TEXT NOT NULL CHECK (migration_kind IN ('sql', 'python')),
  execution_mode   TEXT NOT NULL CHECK (execution_mode IN ('applied', 'baseline')),
  applied_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  applied_by       TEXT NOT NULL DEFAULT current_user,
  git_commit       TEXT CHECK (git_commit IS NULL OR git_commit ~ '^[0-9a-f]{40}$'),
  details          JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE OR REPLACE FUNCTION preclin.reject_schema_migration_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION
    'preclin.schema_migration is append-only; migration % cannot be modified by %',
    OLD.migration_number, lower(TG_OP);
END;
$$;

DROP TRIGGER IF EXISTS trg_schema_migration_append_only
ON preclin.schema_migration;

CREATE TRIGGER trg_schema_migration_append_only
BEFORE UPDATE OR DELETE ON preclin.schema_migration
FOR EACH ROW EXECUTE FUNCTION preclin.reject_schema_migration_mutation();

COMMENT ON TABLE preclin.schema_migration IS
  'Append-only ledger of numbered migration files with their exact SHA-256; baseline rows explicitly identify migrations applied before this ledger existed.';
COMMENT ON COLUMN preclin.schema_migration.execution_mode IS
  'applied = executed by db/migrate.py; baseline = already applied before ledger introduction and explicitly acknowledged.';

COMMIT;
