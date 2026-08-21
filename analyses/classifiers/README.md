# Classifier scripts

The LLM classifiers that produce resumable JSONL / CSV outputs. New audited
JSONL runs are loaded incrementally with `db/13_ingest_llm_outputs.py`;
`db/02_ingest.py` remains the legacy big-bang bootstrap loader.

**Every script here:**
- reads Neon DB state to pick which subjects need scoring
- calls the Anthropic API with a versioned prompt
- writes a resumable JSONL / CSV output
- records the exact system/user prompts, raw response, model parameters,
  provider request id, prompt version, token counts, and USD cost per row

## Setup

```bash
pip install anthropic psycopg2-binary
export ANTHROPIC_API_KEY=sk-ant-...
export DATABASE_URL=postgres://...
```

## The four classifiers

| Script | Purpose | Target file | Default model | Cost per 1k items |
|---|---|---|---|---|
| `score_target_literature.py` | Line B/C/D/E evidence scores per target | `data/target_evidence/literature_scores.jsonl` | Haiku | ~$30-60 |
| `classify_why_stopped.py` | Trial termination classification. First-pass (default) OR verify (`--verify-from PRIOR.jsonl`). | `data/clinical_trials/why_stopped_*.jsonl` | Haiku (first-pass) / Sonnet (verify) | Haiku ~$5-10 / Sonnet ~$20-40 |
| `classify_silent_kill.py` | Ph3+ silent-kill verification per drug | `data/silent_kill_verified.jsonl` | Sonnet | ~$50-150 |
| `nelson_tier_classify.py` | Cohort-wide T-I genetics-tier adjudication | `data/target_evidence/nelson_tiers_*.jsonl` + `.dossiers.jsonl` | Sonnet | depends on cohort/prompt size |

`nelson_tier` is retained for audit and descriptive analysis but is temporarily
excluded from all predictive scorers because existing coverage was selectively
curated on approval-oriented pairs. Do not run a bulk tiering job to restore it
as a model feature: reintroduction requires uniform, indication-specific,
pre-outcome computation and held-out-target validation.

The v4 Nelson workflow enumerates every non-placebo human target-indication
pair represented in `preclin.program`; it does not join approval, outcome, or
phase tables. Before any optional LLM call, it writes a full evidence dossier
containing every Mendelian, ClinGen, GWAS, genetics-only Open Targets, and
canonical PubMed record retrieved. The complete stable dossier content is
stored as an immutable `preclin.source_document`; local dossier JSONL is a
resumable ignored sidecar, not the durable audit record. Evidence below the
default 400,000-character budget is sent intact. Only the oversized tail is
trimmed; indication-term overlap orders overflow rows.

The model still assigns the final T0-T3 tier. Each prompt record carries a
stable evidence ID, a non-binding ontology/text relationship hint, and a
deterministic ceiling describing what it could support if disease-matched.
The model adjudicates disease relevance and cites supporting evidence IDs; a
post-call validator rejects T2 without replicated coding GWAS studies and T3
without qualifying germline Mendelian or Strong/Definitive ClinGen evidence.
T4 is disabled at target-indication level; genetic effect direction is stored
separately from drug-mechanism concordance.

Prepare and inspect the full cohort without spending money:

```bash
.venv/bin/dotenv run -- .venv/bin/python \
  analyses/classifiers/nelson_tier_classify.py \
  --all-clinical --prepare-only \
  --out data/target_evidence/nelson_tiers_all_v4.jsonl
```

Remove `--prepare-only` only after explicit approval for the paid run. The
scoring pass resumes from the saved dossiers, so the exact evidence supplied
to the model is stable. Import caches or cited PMIDs first with
`db/12_ingest_evidence_abstracts.py`; Nelson reads only immutable records in
`preclin.source_document`. Target-linked records and canonical PMIDs cited by
GWAS/Open Targets are both included. Each PubMed excerpt shown to the model is
written to `_source_documents` for ingestion into `preclin.llm_run_source`.

**Canonical cost field is `_cost_usd`.** Older classifier outputs used
`_cost_share` (Sonnet why_stopped verify) or `_cost` (silent_kill,
target_resolution). `db/02_ingest.py:_read_cost` accepts all three for
back-compat, but new runs write only `_cost_usd`.

The cost value is never produced by the LLM — the LLM output schema is
strictly the evidence fields (cat / confidence / rationale / scores /
tier / etc.). `common.py:call_with_retry` reads `resp.usage.input_tokens`
and `resp.usage.output_tokens` from the API response, applies a per-model
price table, and the wrapper appends `_cost_usd` to the row.

## Resumability

Each script skips subjects already present in its output file. Interrupted runs
resume cleanly — just re-invoke with the same `--out`.

## Prompt versioning

Every script has a `PROMPT_VERSION` constant. When the prompt changes:

1. Bump `PROMPT_VERSION` (e.g. `"v1"` → `"v2"`).
2. Rerun with a new output filename.
3. Ingest both files — `preclin.evidence_score` and `preclin.classification` are
   keyed on `(subject_id, dimension, source, source_version)` or
   `(subject_key, classifier_task, classifier_model, classifier_version)`,
   so old and new records coexist. Views resolve to the latest by default.

Never edit prompts in place without a version bump. That kills reproducibility.

New classifier JSONL rows receive a unique `_run_id`. After
`db/10_clinical_trial_source_audit.sql` has been applied,
`db/13_ingest_llm_outputs.py` stores them in `preclin.llm_run`. Verdicts point
to their latest run, while literature extraction runs connect to every
`preclin.evidence_score` fact they produced and retain an immutable
`fact_snapshot` of the run-produced value. Legacy rows remain auditable as
outputs but are explicitly marked as missing their unrecoverable exact inputs.

## Concrete recipe — score the neuroprotection candidates

The scoring diagnostic in `analyses/verify_candidate_scores.py` exposed that
9 of 11 neuroprotection candidates have NULL Line B/C/D/E scores — the model
imputes cohort medians for those. To close the gap:

```bash
# 1. Import the per-target abstract cache into the canonical source store.
#    This is required before a paid call so every prompt excerpt has a stable id.
.venv/bin/dotenv run -- .venv/bin/python db/12_ingest_evidence_abstracts.py \
    --cache-dir /path/to/per-target-jsonl --subject-type target

# 2. Score the 11 candidates' literature evidence
.venv/bin/dotenv run -- .venv/bin/python analyses/classifiers/score_target_literature.py \
    --targets UNC13A,NTRK2,ADCYAP1R1,KL,GALR1,NPY1R,GHSR,VIPR2,APLNR,VGF,CORT \
    --out data/target_evidence/literature_scores_neuro_2026.jsonl

# 3. Prepare complete evidence dossiers for each T-I pair (no paid calls)
.venv/bin/dotenv run -- .venv/bin/python \
  analyses/classifiers/nelson_tier_classify.py \
    --pair UNC13A:ALS \
    --pair NTRK2:Alzheimer \
    --pair ADCYAP1R1:Alzheimer \
    --pair KL:Alzheimer \
    --pair GALR1:Alzheimer \
    --pair NPY1R:Alzheimer \
    --pair GHSR:Parkinson \
    --pair VIPR2:Alzheimer \
    --pair APLNR:Ischemic-stroke \
    --pair VGF:Alzheimer \
    --pair CORT:Alzheimer \
    --prepare-only \
    --out data/target_evidence/nelson_tiers_neuro_v4.jsonl

# 4. After explicit spend approval, rerun the same Nelson command without
#    --prepare-only. It reuses the exact saved dossiers and writes score rows.

# 5. Incrementally ingest the audited literature runs
.venv/bin/dotenv run -- .venv/bin/python db/13_ingest_llm_outputs.py \
    --task target-literature \
    data/target_evidence/literature_scores_neuro_2026.jsonl

# 6. Validate, then atomically ingest the audited Nelson runs and facts
.venv/bin/dotenv run -- .venv/bin/python db/13_ingest_llm_outputs.py \
    --task nelson-tier --dry-run \
    data/target_evidence/nelson_tiers_neuro_v4.jsonl
.venv/bin/dotenv run -- .venv/bin/python db/13_ingest_llm_outputs.py \
    --task nelson-tier data/target_evidence/nelson_tiers_neuro_v4.jsonl

# 7. Update the established database view without rerunning bootstrap views
.venv/bin/dotenv run -- sh -c \
  'psql "$DATABASE_URL" -f db/16_nelson_tier_view.sql'

# 8. Rescore
.venv/bin/dotenv run -- .venv/bin/python analyses/score_neuro_candidates.py
```

Expected total cost: ~$1-3.

## Cost accounting

Every classifier records per-row token counts and USD cost. Aggregate at any
time with e.g.:

```bash
jq -s 'map(._cost_usd // ._cost // ._cost_share) | add' \
  data/target_evidence/literature_scores.jsonl
```

Cumulative spend is also printed after each row while a script runs.

## Not in scope

- **Genome-browser ETL** (gnomAD / GWAS Catalog / DepMap / ClinGen / OMIM /
  STRING / Reactome / SIDER / HPO / DGIdb / HPA / GTEx / Open Targets / IMPC).
  Those tables live in `public.*`, populated by a separate project. This repo
  reads from them but does not rebuild them.
- **Target-literature PubMed abstract discovery.** The scorer reads only
  canonical PubMed snapshots linked to a target in `preclin.source_document`.
  Import per-gene JSONL caches with `db/12_ingest_evidence_abstracts.py` first;
  the scorer fails before any paid call if an input lacks a stable
  `source_document_id`. Trial-linked abstracts are handled separately by
  `db/11_ingest_trial_sources.py`.
- **Broad PubMed searching.** Nelson does not fetch directly. Discovering a
  broad gene/indication corpus remains outside this repository; import records
  with `db/12_ingest_evidence_abstracts.py` so immutable source IDs and hashes
  exist before dossier preparation.

## Adding a new classifier

Follow the existing pattern:

1. Add a script to `analyses/classifiers/`.
2. Use `common.py` for the Anthropic client, retry, JSON extraction, JSONL
   append, and resumability helpers.
3. Define `PROMPT_VERSION` and `DEFAULT_MODEL` at module top.
4. Write audited JSONL that `db/13_ingest_llm_outputs.py` knows how to read,
   or add a new explicit task handler there. CSV-only workflows need their own
   importer because CSV currently drops the exact prompts and raw response.
5. Add a row to the table above.
