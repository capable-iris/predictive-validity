# Predictive Validity

**Benchmark for how well public preclinical evidence predicts clinical drug approval.**

## What exactly is the benchmark evaluating?

**Task:** given a `(target × indication)` hypothesis and 40+ dimensions of public preclinical evidence (human genetics, tissue expression, cell essentiality, animal models, safety, landscape), predict `P(any drug on this target-indication pair gets FDA-approved for THIS specific indication within our 10-year observation window)`.

**Unit of analysis:** `(target × indication)` pair — a scientific hypothesis, not a specific drug. Multiple drug programs may test the same T-I hypothesis; the model predicts whether *any* of them succeeds for that specific indication.

**Cohort — "Phase 1+ target-matched T-I pairs" means:**
- **T-I pair**: a specific `(target_gene, indication)` combination. Example: `(EGFR, non-small cell lung cancer)`.
- **Target-matched**: at least one drug developed against this T-I has a resolvable primary target in genome-browser's target catalog. Excludes: placebos, vaccines, cell therapies without a molecular target, unresolved compound codes.
- **Phase 1+**: at least one drug program targeting this T-I entered a clinical trial. Excludes preclinical-only hypotheses.
- **Result: 13,639 T-I pairs, base rate 2.95%** (403 approved).

**Ground truth ("strict per-indication outcome"):** was any drug hitting this target ever FDA-approved *specifically for this indication*? Not "approved for anything" — that would count e.g. EGFR-approved-for-lung as a positive for `(EGFR, colorectal)`. Strict outcome only counts approval on the exact indication.

**Evidence dimensions (features):** 40+ per target/T-I. Categories: A. Genetics (Nelson tier, ClinGen, Mendelian, GWAS, Open Targets), B. Mechanistic (tractability, tissue Tau, Reactome, PPI, GO), C. Cell (DepMap essentiality, cell literature), D. Animal (IMPC KO phenotypes, Open Targets animal model), E. Human PD engagement (literature score), H. Safety (gnomAD pLI/LOEUF), I. Landscape (family precedent, DGIdb).

**Evaluation:** 5-fold GroupKFold on `target_id` — no target appears in both train and test folds. Tests whether the model has learned generalizable biology or is memorizing target-specific shortcuts.

## Headline result

| Metric | Value |
|---|---|
| **AUC** | **0.825 [0.797, 0.849]** |
| **RS(top 10%)** | **13.67** (top decile enriched 13.7× for approvals) |
| Recall @ top 10% | 0.60 (top-scored 10% captures 60% of all approvals) |
| ECE | 0.013 (well-calibrated) |

Best model: stacked ensemble (LogReg + regularized LightGBM + RandomForest).

Comparison:

| Method | AUC | Gap vs best |
|---|---|---|
| Stacked ensemble (trained on our data) | **0.825** | — |
| Untrained Pheiron RS composite | 0.615 | −21pp |
| Sonnet LLM agent (reads evidence dossier) | 0.633 | −19pp |
| Random baseline | 0.500 | −33pp |

**Trained ML extracts substantially more signal than published rule-based methodology or LLM reasoning.**

Full leaderboard + robustness + pathway wrongness: **[`RESULTS.md`](RESULTS.md)**.

## Key finding

**Genetic evidence alone accounts for ~18pp of the model's AUC.** Removing all genetics features drops the model from 0.829 to 0.651. Removing target-level cell or animal literature drops AUC by exactly 0.0pp — a clean measurement of publication-bias saturation on target-level literature scores.

Corollary from the pathway-wrongness analysis: even at Phase 3 with strong genetic + cell + animal + PD evidence all high, **~78% of drug programs still fail**. Preclinical biology confirms the drug's mechanism works; it doesn't confirm the mechanism drives the clinical endpoint.

## Quick start

```bash
git clone git@github.com:dryingpaint/predictive-validity.git
cd predictive-validity
cp .env.example .env       # add DATABASE_URL

pip install psycopg2-binary scikit-learn numpy lightgbm anthropic

# Explore live leaderboard
psql "$DATABASE_URL" -c "SELECT * FROM preclin.v_benchmark_leaderboard"

# Reproduce the headline AUC 0.825 (~5 min)
python3 analyses/final_benchmark.py
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
│   └── classifiers/     LLM classifiers that produce the JSONL inputs 02_ingest.py reads
│                        (target-lit scorer, why-stopped classifier, silent-kill verify,
│                         Nelson tier assignment) — see analyses/classifiers/README.md
```

## How to plug in your own model

**Path 1** — in-process Python: implement the scorer interface, `register_scorer(name, fn)`, run `python3 benchmark/runner.py <your_scorer_name>`.

**Path 2** — external CSV: produce `(target_id, indication_id, predicted_p_approval)` rows, wire in via `wire_external_scores()` in `benchmark/external_template.py`.

Either way, results appear in `preclin.v_benchmark_leaderboard`.

## What we CAN claim (with statistical support)

1. Public preclinical evidence predicts strict per-T-I FDA approval at **AUC 0.825** on Phase 1+ target-matched cohort, held-out-target CV.
2. **Top-decile predictions are 13.7× enriched** for approvals.
3. Model is well-calibrated (**ECE 0.013**).
4. **Human genetic evidence is dominant** (17.7pp of AUC).
5. **Target-level cell + animal literature contribute zero marginal signal** on top of genetics + safety.
6. Model **generalizes to unseen targets** (2pp drop between random-split and held-out-target).
7. Model **generalizes out-of-time** (LogReg trained pre-2019 predicts 2019+ outcomes at AUC 0.77, RS 12.3).
8. **Trained ML beats published rule-based methodology by 21pp AUC** and beats LLM-agent scoring by 19pp.

## What we CANNOT claim

- Absolute `p_approval` values are cohort-scoped (base rate 2.95% in our cohort; not comparable to a random drug in the world).
- Non-CT.gov trials (EU-CTR, ChiCTR) ≈ 20% of global drug development activity — not ingested.
- Preclinical / IND-stage kills invisible (never enter CT.gov).
- Feature values are current-day for reference dimensions (Nelson tier, gnomAD, ClinGen); only trial-precedent features are time-cutoff-aware.

Full caveats: [`RESULTS.md#robustness—12-attacks`](RESULTS.md).

## License

MIT. If you build something on top, please cite / link back.
