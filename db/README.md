# `preclin.*` schema — runbook

The `preclin.*` schema in Neon Postgres is the single source of truth for the analysis. This directory has schema DDL and ingest scripts.

For example SQL queries, see [`QUESTIONS.md`](QUESTIONS.md).

## Connect

```bash
export DATABASE_URL='postgresql://...'   # ask a maintainer for credentials
psql "$DATABASE_URL"
\dt preclin.*                            # list tables
\dv preclin.*                            # list views
```

## First-time setup

```bash
psql "$DATABASE_URL" -f 01_schema.sql
python3 02_ingest.py               # big-bang ingest from local JSONL/CSV
psql "$DATABASE_URL" -f 03_views.sql
python3 04_ingest_extra.py         # tissue, pleiotropy, pathways, DGIdb, HPO
psql "$DATABASE_URL" -f 05_ti_views.sql
python3 06_ingest_more.py          # single-cell Tau + GO
psql "$DATABASE_URL" -f 07_analysis_views.sql
psql "$DATABASE_URL" -f 08_strict_outcome_view.sql
psql "$DATABASE_URL" -f 09_time_cutoff_features.sql
```

~15 min end-to-end for a fresh ingest.

## Existing database migrations

Run migrations from the repository root. Fresh ingests already contain these
changes in the numbered setup files.

```bash
.venv/bin/dotenv run -- sh -c 'psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f db/10_reclassify_hpo_evidence.sql'
```

## Tables

| Table | Rows | Purpose |
|---|---|---|
| `preclin.drug` | 52,694 | Canonical drug identity |
| `preclin.drug_target` | 36,686 | Drug→target multi-source junction |
| `preclin.indication` | 8,875 | Canonical indications |
| `preclin.program` | 76,974 | (drug × indication × sponsor) — analytical unit |
| `preclin.program_trial` | 88,999 | Program → CT.gov trial junction |
| `preclin.program_outcome` | 76,974 | Rollup: approved / efficacy_fail / silent_kill / etc. |
| `preclin.approval` | 544 | FDA approvals with Nelson tier |
| `preclin.evidence_score` | ~250,000 | LONG-form evidence facts |
| `preclin.classification` | ~13,000 | LLM outputs (why_stopped, silent-kill, target resolution) |
| `preclin.evidence_dimension` | 40 | Registry of every evidence dimension |
| `preclin.benchmark_run` | ~70 | Benchmark leaderboard rows |
| `preclin.benchmark_prediction` | ~40,000 | Per-(scorer × T-I) predictions |

## Views

- `v_program_evidence_wide` — flat master, one row per program with all evidence + outcome
- `v_target_indication_program` — Pheiron-style T-I unit (loose outcome)
- `v_target_indication_strict_outcome` — strict per-T-I outcome (approved for THIS indication)
- `v_target_evidence_wide` — all evidence per target (wide-form)
- `v_target_family_precedent_by_year` — time-cutoff-aware family/gene precedent
- `v_relative_success_clean` — Pheiron RS metric per dimension (placebos filtered)
- `v_pathway_wrongness` — Phase 3 fail rate per evidence tier
- `v_combination_evidence` — pairwise RS(A ∧ B) lift
- `v_benchmark_leaderboard` — scorer comparison
- `v_dimension_coverage` — coverage per dimension × subject

## Add a new evidence dimension

```sql
INSERT INTO preclin.evidence_dimension (dimension, category, subject_type, data_type, description)
VALUES ('my_new_dim', 'C_cell', 'target', 'numeric_float', 'What this measures');

INSERT INTO preclin.evidence_score
  (subject_type, subject_id, dimension, category, value_numeric, source, source_version, extracted_by)
SELECT 'target', target_id, 'my_new_dim', 'C_cell', <value>, 'my_source', '2026-07', 'script:mine'
FROM ...;
```

Then rebuild views to include it in `v_target_evidence_wide` (edit `05_ti_views.sql`, re-run).

## Reset

```sql
DROP SCHEMA preclin CASCADE;
CREATE SCHEMA preclin;
-- then re-run 01_schema.sql onwards
```
