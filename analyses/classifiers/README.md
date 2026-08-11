# Classifier scripts

The LLM classifiers that produce the JSONL / CSV inputs consumed by
`db/02_ingest.py`. These are the "Phase 2" pipeline referenced in the top-level
README's data flow — without them, the `preclin.evidence_score` and
`preclin.classification` tables can't be extended to new targets or refreshed.

**Every script here:**
- reads Neon DB state to pick which subjects need scoring
- calls the Anthropic API with a versioned prompt
- writes a resumable JSONL / CSV that `db/02_ingest.py` can ingest as-is
- records model, prompt version, token counts, and USD cost per row

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
| `nelson_tier_classify.py` | T-I Nelson tier assignment | `data/target_evidence/nelson_tiers_batch_YYYYMMDD.csv` | Sonnet | ~$50 |

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

## Concrete recipe — score the neuroprotection candidates

The scoring diagnostic in `analyses/verify_candidate_scores.py` exposed that
9 of 11 neuroprotection candidates have NULL Line B/C/D/E scores — the model
imputes cohort medians for those. To close the gap:

```bash
# 1. Score the 11 candidates' literature evidence
python3 analyses/classifiers/score_target_literature.py \
    --targets UNC13A,NTRK2,ADCYAP1R1,KL,GALR1,NPY1R,GHSR,VIPR2,APLNR,VGF,CORT \
    --out data/target_evidence/literature_scores_neuro_2026.jsonl

# 2. Assign Nelson tiers for each T-I pair
python3 analyses/classifiers/nelson_tier_classify.py \
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
    --out data/target_evidence/nelson_tiers_batch_neuro_2026.csv

# 3. Ingest — 02_ingest.py picks up both files automatically
python3 db/02_ingest.py

# 4. Rescore
python3 analyses/score_neuro_candidates.py
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
- **PubMed abstract fetching.** `score_target_literature.py` will read from a
  `preclin.pubmed_target_abstract(gene, pmid, title, abstract)` cache table if
  one exists, or from a per-gene JSONL cache directory. Providing that cache
  is outside these scripts' scope — use NCBI EFetch or a paid abstract feed.

## Adding a new classifier

Follow the existing pattern:

1. Add a script to `analyses/classifiers/`.
2. Use `common.py` for the Anthropic client, retry, JSON extraction, JSONL
   append, and resumability helpers.
3. Define `PROMPT_VERSION` and `DEFAULT_MODEL` at module top.
4. Write to a JSONL / CSV that `db/02_ingest.py` (or a new sibling ingest
   script) knows how to read.
5. Add a row to the table above.
