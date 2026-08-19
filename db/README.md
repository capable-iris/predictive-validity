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

From the repository root:

```bash
.venv/bin/dotenv run -- sh -c 'psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f db/01_schema.sql'
.venv/bin/dotenv run -- .venv/bin/python db/02_ingest.py
.venv/bin/dotenv run -- sh -c 'psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f db/03_views.sql'
.venv/bin/dotenv run -- .venv/bin/python db/04_ingest_extra.py
.venv/bin/dotenv run -- sh -c 'psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f db/05_ti_views.sql'
.venv/bin/dotenv run -- .venv/bin/python db/06_ingest_more.py
.venv/bin/dotenv run -- sh -c 'psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f db/07_analysis_views.sql'
.venv/bin/dotenv run -- sh -c 'psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f db/08_strict_outcome_view.sql'
.venv/bin/dotenv run -- sh -c 'psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f db/09_time_cutoff_features.sql'
.venv/bin/dotenv run -- sh -c 'psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f db/10_clinical_trial_source_audit.sql'
.venv/bin/dotenv run -- .venv/bin/python db/11_ingest_trial_sources.py --scope classified --limit 100
```

The core database setup takes ~15 minutes; source ingestion time depends on the
selected trial scope.

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
| `preclin.llm_run` | varies | Append-only exact prompt/response records behind classifications and evidence facts |
| `preclin.source_document` | varies | Immutable CT.gov, PubMed, and other source snapshots |
| `preclin.source_document_subject` | varies | Trial/target/drug→source provenance links |
| `preclin.llm_run_source` | varies | Exact source excerpts supplied to each model call |
| `preclin.llm_run_evidence_score` | varies | Model run→current fact link plus immutable run-produced value snapshot |
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
- `v_trial_source_latest` — latest full registry/publication text linked to each NCT id
- `v_classification_audit` — verdict + exact model input/output + exact/available source links
- `v_evidence_score_audit` — current fact, immutable run-produced value, extraction run, and exact abstract excerpts

## Ingest and audit clinical-trial sources

Apply the source/audit migration, then start with only trials that already have
LLM verdicts:

```bash
.venv/bin/dotenv run -- sh -c 'psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f db/10_clinical_trial_source_audit.sql'

# NCBI asks E-utilities clients to identify themselves with an email.
# Put NCBI_EMAIL (and optionally NCBI_API_KEY) in .env.
.venv/bin/dotenv run -- .venv/bin/python db/11_ingest_trial_sources.py \
  --scope classified --limit 100
```

The default 30-day freshness window makes the command resumable. Identical
payloads deduplicate by SHA-256 and advance `last_seen_at`; changed upstream
records create a new immutable version. After validating the first batch, use
`--limit 0` for the whole scope.
ClinicalTrials.gov fetches use four workers by default, with request starts
globally capped at five per second and transient HTTP errors retried with
backoff; use `--workers 1` for a fully sequential run.
`--scope program` covers all trials linked to benchmark programs. Avoid
`--scope all` unless a complete ClinicalTrials.gov mirror is intentional.

No LLM is called by this ingest. PubMed abstracts are stored with an explicit
rights notice because some abstract text may be third-party copyrighted.

The source tables are the canonical abstract store going forward; a separate
local abstract cache is not required. Classifiers still write resumable JSONL
so paid calls survive interruption. Load those completed runs with the narrow
incremental importer rather than rerunning the big-bang `02_ingest.py`:

```bash
# Trial why-stopped verdicts
.venv/bin/dotenv run -- .venv/bin/python db/13_ingest_llm_outputs.py \
  --task why-stopped data/clinical_trials/why_stopped_2026.jsonl

# Target-level PubMed evidence tiers
.venv/bin/dotenv run -- .venv/bin/python db/13_ingest_llm_outputs.py \
  --task target-literature data/target_evidence/literature_scores_2026.jsonl
```

The importer also accepts `--task silent-kill` and `--task drug-evidence`.
It requires the exact audit fields emitted by `analyses/classifiers/common.py`,
commits all supplied files atomically, and supports `--dry-run`. It never calls
an LLM or retrieves a source. `02_ingest.py` remains unchanged as the manual
bootstrap loader for the historical local data bundle.

Target-literature scoring refuses to call a paid model unless every supplied
abstract is already represented by a canonical `source_document_id`. Import a
legacy cache with `db/12_ingest_evidence_abstracts.py` before scoring; this
ensures the completed JSONL can always pass the audited importer.

Backfill abstracts used for target/drug evidence extraction separately:

```bash
# Refetch every PMID that historical evidence rows retained (start with 100).
.venv/bin/dotenv run -- .venv/bin/python db/12_ingest_evidence_abstracts.py \
  --from-citations --limit 100

# Prefer this when the original per-target cache still exists: it includes
# abstracts supplied to the model that were not selected as notable citations.
.venv/bin/dotenv run -- .venv/bin/python db/12_ingest_evidence_abstracts.py \
  --cache-dir /path/to/per-target-jsonl --subject-type target
```

All full abstracts go in `preclin.source_document.abstract_text`; raw PubMed XML
or cache payloads remain in `raw_content_text` / `raw_content`. The exact
possibly-truncated string sent to a model goes in
`preclin.llm_run_source.excerpt_text`. This distinction lets an auditor compare
the complete source with precisely what the model saw. Each evidence link also
stores `fact_snapshot`, so rerunning the same prompt version may update the
current `evidence_score` projection without rewriting what an older run
produced.

Migration 10 groups existing PubMed-derived facts into synthetic legacy runs,
but marks them `exact_input_unavailable`. `--from-citations` can recover and
link the PMIDs retained by old target-level outputs as `reported_citation`;
those links are useful provenance, not a claim that the full abstract was
definitely present in the discarded prompt. For complete historical recovery,
import the original abstract caches. Drug-specific facts retained no PMIDs, so
their old source set requires the original cache or a rerun.

Audit a verdict with:

```sql
SELECT subject_key, category, confidence, has_exact_input,
       system_prompt, user_prompt, raw_response,
       exact_input_sources, available_trial_sources
FROM preclin.v_classification_audit
WHERE classifier_task = 'why_stopped' AND subject_key = 'NCT01234567';
```

Rows created before this migration have `has_exact_input = false`: the parsed
legacy JSON is preserved, but the old prompt cannot be reconstructed honestly.
For source-level inspection, `preclin.v_trial_source_latest.why_stopped_text`
is the verbatim registry field and `.abstract_text` is the PubMed abstract;
`raw_content` / `raw_content_text` retain the complete JSON/XML payloads.

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
