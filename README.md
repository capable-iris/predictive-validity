# Predictive Validity

**Benchmark for how well public preclinical evidence predicts clinical drug approval.**

## What exactly is the benchmark evaluating?

**Task:** given a `(target × indication)` hypothesis and 40+ dimensions of public preclinical evidence (human genetics, tissue expression, cell essentiality, animal models, safety, landscape), predict `P(any drug on this target-indication pair gets FDA-approved for THIS specific indication within our 10-year observation window)`.

**Unit of analysis:** `(target × indication)` pair — a scientific hypothesis, not a specific drug. Multiple drug programs may test the same T-I hypothesis; the model predicts whether *any* of them succeeds for that specific indication.

**Cohort — "Phase 1+ target-matched T-I pairs" means:**
- **T-I pair**: a specific `(target_gene, indication)` combination. Example: `(EGFR, non-small cell lung cancer)`.
- **Target-matched**: at least one non-placebo drug developed against this T-I has one uniquely strongest primary target after excluding explicit FDA-approval mappings and applying source priority, confidence, and independent-source corroboration. Exact strongest ties are excluded because their outcomes cannot be attributed to one target without guessing.
- **Phase 1+**: at least one drug program targeting this T-I entered a clinical trial. Excludes preclinical-only hypotheses.
- **Result: 10,685 T-I pairs, base rate 3.14%** (336 approved; 792 targets).

**Ground truth ("strict per-indication outcome"):** was any drug hitting this target ever FDA-approved *specifically for this indication*? Not "approved for anything" — that would count e.g. EGFR-approved-for-lung as a positive for `(EGFR, colorectal)`. Strict outcome only counts approval on the exact indication.

**Evidence dimensions (features):** 40+ per target/T-I. Categories: A. Human genetics (ClinGen, Mendelian, GWAS, positively observed terms in the HPO Phenotypic abnormality branch, Open Targets), B. Mechanistic (tractability, tissue Tau, Reactome, PPI, GO), C. Cell (DepMap essentiality, cell literature), D. Animal (IMPC KO phenotypes, Open Targets animal model), E. Human PD engagement (literature score), H. Safety (gnomAD pLI/LOEUF), I. Landscape (pleiotropy, DGIdb).

**Temporary Nelson-tier exclusion:** every pair in the 12,142-pair clinical
universe has an adjudicated tier, but `nelson_tier` remains outside the
canonical predictor because its current-day GWAS/ClinGen inputs can postdate
clinical outcomes. It is reported as an explicit sensitivity analysis.

**Evaluation:** 5-fold GroupKFold on `target_id` — no target appears in both train and test folds. Tests whether the model has learned generalizable biology or is memorizing target-specific shortcuts.

## Headline result

| Metric | Value |
|---|---|
| **AUC** | **0.570 [0.515, 0.620]** |
| **RS(top 10%)** | **1.46** |
| Recall @ top 10% | 0.140 |
| ECE | 0.002 |

Canonical calibrated model: stacked ensemble (LogReg + regularized LightGBM + RandomForest).

The interval resamples complete targets (1,000 iterations; seed 42). A matched
2,000-iteration random target-ranking control has median AUC 0.501 and 95% null
range 0.459–0.545; only 2/2,000 random rankings reached the headline AUC
(empirical p=0.0015). A constant-prevalence calibration control has AUC 0.500
and Brier score 0.03046.

Comparison:

| Method | AUC | Gap vs best |
|---|---|---|
| LogReg L2 | **0.571** | — |
| Stacked ensemble | 0.570 | −0.1pp |

Older rule-based and LLM comparisons in `data/leaderboard.csv` predate the Nelson exclusion and are retained only as historical run records; they are not directly comparable to this regenerated result.

On identical corrected rows and folds, adding the complete Nelson tier raises
stacked AUC from 0.570 to 0.602 (paired ΔAUC +0.032, target-bootstrap 95% CI
+0.007 to +0.059). This is a sensitivity result, not the headline, because the
underlying evidence is not uniformly frozen before clinical outcomes.

Full leaderboard + robustness + pathway wrongness: **[`RESULTS.md`](RESULTS.md)**.

## Key finding

The approval-independent consensus-target headline reaches AUC 0.570 for the
calibrated stacked model (LogReg 0.571) without Nelson. On the same rows and
folds, the current-day Nelson sensitivity reaches 0.602 (LogReg 0.612), but the
project does not treat that increment as prospective evidence because its
GWAS/ClinGen inputs are not fully time-frozen. Category ablations from older
cohorts are retained as historical results and need rerunning.

Corollary from the pathway-wrongness analysis: even at Phase 3 with strong genetic + cell + animal + PD evidence all high, **~78% of drug programs still fail**. Preclinical biology confirms the drug's mechanism works; it doesn't confirm the mechanism drives the clinical endpoint.

## Quick start

```bash
git clone git@github.com:dryingpaint/predictive-validity.git
cd predictive-validity
cp .env.example .env       # add DATABASE_URL

pip install psycopg2-binary scikit-learn numpy lightgbm anthropic openai

# Explore live leaderboard
psql "$DATABASE_URL" -c "SELECT * FROM preclin.v_benchmark_leaderboard"

# Reproduce the headline AUC 0.570 (~5 min)
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
├── db/                  Ordered bootstrap, immutable migrations, mutable ingesters, and schema docs
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

1. Public preclinical evidence predicts strict per-T-I FDA approval at **AUC 0.570 [0.515, 0.620]** under target-cluster bootstrap for the calibrated stacked model on the approval-independent consensus-target Phase 1+ cohort with held-out-target CV.
2. Top-decile predictions have **RS 1.46** for approvals.
3. The stacked model is well calibrated on this cohort (**ECE 0.002**).
4. A complete current-day Nelson tier adds predictive signal in a paired sensitivity analysis, but the timing analysis prevents interpreting it as a prospective causal effect.

## What we CANNOT claim

- Absolute `p_approval` values are cohort-scoped (base rate 3.14% in our cohort; not comparable to a random drug in the world).
- Non-CT.gov trials (EU-CTR, ChiCTR) ≈ 20% of global drug development activity — not ingested.
- Preclinical / IND-stage kills invisible (never enter CT.gov).
- Feature values are current-day for reference dimensions such as gnomAD and ClinGen; only trial-precedent features are time-cutoff-aware. `nelson_tier` is excluded from predictive models.

Full caveats: [`RESULTS.md#robustness-and-limitations`](RESULTS.md#robustness-and-limitations).

## License

MIT. If you build something on top, please cite / link back.
