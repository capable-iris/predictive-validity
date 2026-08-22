# Predictive Validity

**Benchmark for how well public preclinical evidence predicts clinical drug approval.**

## What exactly is the benchmark evaluating?

**Task:** given a `(target × indication)` hypothesis and 40+ dimensions of public preclinical evidence (human genetics, tissue expression, cell essentiality, animal models, safety, landscape), predict `P(any drug on this target-indication pair gets FDA-approved for THIS specific indication within our 10-year observation window)`.

**Unit of analysis:** `(target × indication)` pair — a scientific hypothesis, not a specific drug. Multiple drug programs may test the same T-I hypothesis; the model predicts whether *any* of them succeeds for that specific indication.

**Cohort — "Phase 1+ target-matched T-I pairs" means:**
- **T-I pair**: a specific `(target_gene, indication)` combination. Example: `(EGFR, non-small cell lung cancer)`.
- **Target-matched**: at least one drug developed against this T-I has a resolvable primary target in genome-browser's target catalog. Excludes: placebos, vaccines, cell therapies without a molecular target, unresolved compound codes.
- **Phase 1+**: at least one drug program targeting this T-I entered a clinical trial. Excludes preclinical-only hypotheses.
- **Result: 13,821 T-I pairs, base rate 2.92%** (404 approved).

**Ground truth ("strict per-indication outcome"):** was any drug hitting this target ever FDA-approved *specifically for this indication*? Not "approved for anything" — that would count e.g. EGFR-approved-for-lung as a positive for `(EGFR, colorectal)`. Strict outcome only counts approval on the exact indication.

**Evidence dimensions (features):** 40+ per target/T-I. Categories: A. Human genetics (ClinGen, Mendelian, GWAS, positively observed terms in the HPO Phenotypic abnormality branch, Open Targets), B. Mechanistic (tractability, tissue Tau, Reactome, PPI, GO), C. Cell (DepMap essentiality, cell literature), D. Animal (IMPC KO phenotypes, Open Targets animal model), E. Human PD engagement (literature score), H. Safety (gnomAD pLI/LOEUF), I. Landscape (family precedent, DGIdb).

**Temporary Nelson-tier exclusion:** `nelson_tier` remains stored for audit and descriptive analysis but is excluded from every predictive model. Its selectively curated coverage is strongly associated with approval status. It may be reconsidered only after uniform, indication-specific, pre-outcome computation across the cohort and held-out-target validation.

**Evaluation:** 5-fold GroupKFold on `target_id` — no target appears in both train and test folds. Tests whether the model has learned generalizable biology or is memorizing target-specific shortcuts.

## Headline result

| Metric | Value |
|---|---|
| **AUC** | **0.653 [0.622, 0.680]** |
| **RS(top 10%)** | **3.12** |
| Recall @ top 10% | 0.257 |
| ECE | 0.001 |

Best model: stacked ensemble (LogReg + regularized LightGBM + RandomForest).

Comparison:

| Method | AUC | Gap vs best |
|---|---|---|
| Stacked ensemble | **0.653** | — |
| LogReg L2 | 0.643 | −1.0pp |

Older rule-based and LLM comparisons in `data/leaderboard.csv` predate the Nelson exclusion and are retained only as historical run records; they are not directly comparable to this regenerated result.

On the same corrected Phase 1+ cohort and held-out-target split, excluding Nelson reduced stacked AUC from 0.821 to 0.653. This confirms that the previous headline was substantially driven by annotation-selection leakage and supersedes it.

Full leaderboard + robustness + pathway wrongness: **[`RESULTS.md`](RESULTS.md)**.

## Key finding

On the Phase 2+ held-out-target ablation, removing human PD evidence reduces LogReg AUC by 2.5pp, removing genetics reduces it by 1.9pp, and removing mechanistic evidence reduces it by 1.3pp. Removing target-level cell or animal evidence slightly improves AUC, so neither shows positive marginal signal in the corrected model.

Corollary from the pathway-wrongness analysis: even at Phase 3 with strong genetic + cell + animal + PD evidence all high, **~78% of drug programs still fail**. Preclinical biology confirms the drug's mechanism works; it doesn't confirm the mechanism drives the clinical endpoint.

## Quick start

```bash
git clone git@github.com:dryingpaint/predictive-validity.git
cd predictive-validity
cp .env.example .env       # add DATABASE_URL

pip install psycopg2-binary scikit-learn numpy lightgbm anthropic openai

# Explore live leaderboard
psql "$DATABASE_URL" -c "SELECT * FROM preclin.v_benchmark_leaderboard"

# Reproduce the headline AUC 0.653 (~5 min)
.venv/bin/dotenv run -- .venv/bin/python analyses/final_benchmark.py
```

## Repo structure

```
predictive-validity/
├── README.md            ← you are here
├── RESULTS.md           ← full leaderboard, ablation, pathway wrongness, robustness
├── CASE_STUDIES.md      6 preclinical-strong / clinical-fail drug case studies
├── CONTEXT_FDA.md       FDA approvals landscape + failure-reason breakdown
├── data/                CSV snapshots (approvals + leaderboard) + charts
├── db/                  Postgres schema + ingest + SCHEMA.md (evidence taxonomy)
├── benchmark/           Scoring framework — 5 scorer files + runner
├── analyses/            Reproducible analysis scripts (ablation, time-machine, etc.)
│   └── classifiers/     LLM classifiers that produce incrementally ingested audit JSONL
│                        (target-lit scorer, why-stopped classifier, silent-kill verify,
│                         Nelson tier assignment for descriptive/audit use only)
```

## How to plug in your own model

**Path 1** — in-process Python: implement the scorer interface, `register_scorer(name, fn)`, run `python3 benchmark/runner.py <your_scorer_name>`.

**Path 2** — external CSV: produce `(target_id, indication_id, predicted_p_approval)` rows, wire in via `wire_external_scores()` in `benchmark/external_template.py`.

Either way, results appear in `preclin.v_benchmark_leaderboard`.

## What we CAN claim (with statistical support)

1. Public preclinical evidence predicts strict per-T-I FDA approval at **AUC 0.653** on the Phase 1+ target-matched cohort with held-out-target CV.
2. Top-decile predictions are **3.12× enriched** for approvals.
3. The stacked model is well calibrated on this cohort (**ECE 0.001**).
4. Human PD, genetics, and mechanistic evidence provide modest positive marginal signal in the Phase 2+ LogReg ablation.
5. Target-level cell and animal evidence provide no positive marginal signal in that ablation.

## What we CANNOT claim

- Absolute `p_approval` values are cohort-scoped (base rate 2.92% in our cohort; not comparable to a random drug in the world).
- Non-CT.gov trials (EU-CTR, ChiCTR) ≈ 20% of global drug development activity — not ingested.
- Preclinical / IND-stage kills invisible (never enter CT.gov).
- Feature values are current-day for reference dimensions such as gnomAD and ClinGen; only trial-precedent features are time-cutoff-aware. `nelson_tier` is excluded from predictive models.

Full caveats: [`RESULTS.md#robustness-and-limitations`](RESULTS.md#robustness-and-limitations).

## License

MIT. If you build something on top, please cite / link back.
