# Results

**Refreshed 2026-08-17:** headline, strict-cohort robustness, ablation,
time-machine, per-modality, HPO relative-success, and leaderboard results were
rerun after correcting HPO phenotype breadth. Paid LLM scoring and the
descriptive pathway-wrongness section were not rerun.

## Headline

**Predicting FDA approval for a specific `(target × indication)` pair from public preclinical evidence:**

Best model: stacked ensemble (LogReg + regularized LightGBM + RandomForest), evaluated with 5-fold GroupKFold on `target_id` (no target appears in both train and test).

| Metric | Value |
|---|---|
| **AUC** | **0.821 [0.795, 0.847]** |
| **RS (top 10%)** | **13.31** (top decile enriched 13.3× for approvals) |
| **Recall @ top 10%** | 0.597 |
| ECE | 0.012 (well-calibrated) |
| Cohort | Phase 1+ target-matched T-I pairs, n=13,821 |
| Base rate | 2.92% (404 approvals) |

## Full leaderboard — strict per-T-I outcome

Phase 1+ cohort (n=13,821), 5-fold GroupKFold on `target_id`:

| Rank | Scorer | AUC (95% CI) | RS(top 10%) | ECE | R@10% | P@10% |
|---|---|---|---|---|---|---|
| 1 | stacked_final_v2_hpo | **0.821 [0.795, 0.847]** | **13.31** | 0.012 | 0.597 | 0.174 |
| 2 | logreg_final_v2_hpo | 0.818 [0.789, 0.846] | 13.73 | 0.269 | 0.604 | 0.177 |
| 3 | logreg_interactions_v2_hpo | 0.815 [0.790, 0.842] | 11.66 | 0.273 | 0.564 | 0.165 |
| 4 | stacked_family_v2_hpo | 0.814 [0.787, 0.839] | 12.26 | 0.014 | 0.577 | 0.169 |
| 5 | logreg_family_v2_hpo | 0.812 [0.788, 0.838] | 13.45 | 0.264 | 0.599 | 0.175 |
| 6 | xgboost_final_v2_hpo | 0.806 [0.778, 0.831] | 13.04 | 0.107 | 0.592 | 0.173 |
| 7 | catboost_final_v2_hpo | 0.806 [0.769, 0.833] | 12.39 | 0.218 | 0.579 | 0.169 |

**External-model comparisons (strict Ph2+, cohort n=8,130):**

| Scorer | Method | AUC (95% CI) | RS(top 10%) |
|---|---|---|---|
| logreg_holdout_target_v2_hpo | Trained LogReg, GroupKFold(target) | 0.819 [0.795, 0.846] | 12.26 |
| pheiron_rs_composite_v2_hpo | Untrained published RS | 0.611 [0.587, 0.648] | 5.66 |

**Trained ML beats the refreshed published rule-based methodology by 20.8pp AUC.**
The paid LLM-agent comparison was not rerun for this correction.

## Strict-cohort robustness

Best model per variant:

| Cohort variant | n | Base rate | Best AUC | RS(top 10%) |
|---|---|---|---|---|
| Strict, Ph2+, random-split | 8,130 | 5.0% | 0.820 (stack) | 12.51 |
| Strict, Ph2+, held-out target | 8,130 | 5.0% | 0.819 (LogReg) | 12.26 |
| Strict, Ph2+ time-machine 2019 | 3,597 | 0.75% | 0.796 (LogReg) | 21.36 |
| Strict, Ph1+, random-split | 13,821 | 2.92% | 0.836 (stack) | 14.46 |
| **Strict, Ph1+, held-out target** | **13,821** | **2.92%** | **0.821 (stack)** | **13.31** |

Loose-outcome rows from the prior report were omitted because they were not
part of this strict-outcome refresh.

## Ablation — what makes the AUC

Full LogReg (strict Ph2+, GroupKFold by target) = 0.819. Leave-one-category-out:

| Removed category | Remaining AUC | ΔAUC |
|---|---|---|
| **A. Human genetics** | 0.620 | **−19.9pp** — dominant |
| Therapeutic-area context | 0.806 | −1.3pp |
| B. Mechanistic | 0.810 | −0.9pp |
| H. Safety | 0.816 | −0.3pp |
| D. Animal | 0.818 | −0.2pp |
| I. Landscape | 0.820 | +0.0pp |
| E. Human PD | 0.820 | +0.1pp |
| C. Cell | 0.822 | +0.3pp |

Human genetics accounts for ~20pp of AUC. Target-level cell evidence has no
positive marginal contribution in this model, while animal evidence adds only
~0.2pp. HPO phenotype breadth is directionally coherent as a pleiotropy risk
(RS 0.72; LogReg coefficient −0.177).

## Per-modality (STRICT, LogReg, GroupKFold by target)

| Modality | n | AUC (95% CI) | RS(top 10%) |
|---|---|---|---|
| biologic (mAb/protein/peptide) | 884 | 0.817 [0.774, 0.861] | 10.50 |
| small_molecule | 1,100 | 0.797 [0.764, 0.837] | 6.95 |

The confidence intervals overlap. Genetic-medicine and cell-therapy cohorts
remain too small for stable CV.

## Pathway wrongness — how often does strong evidence still fail?

Conditional-failure view: for T-I pairs with strong evidence in each dimension, what fraction of Phase 3+ attempts still fail?

**Phase 3+ T-I pairs (n=1,182):**

| Evidence dimension | high-ev n | Approved | Efficacy fail | **Any fail** |
|---|---|---|---|---|
| Line C lit high (target cell) | 1,053 | 21% | 50% | **79%** |
| Line D lit high (target animal) | 1,004 | 22% | 51% | **78%** |
| OT genetic ≥0.3 | 986 | 17% | 54% | **83%** |
| OT animal model ≥0.3 | 955 | 15% | 54% | **85%** |
| Line E lit high (human PD) | 855 | 27% | 47% | **73%** |
| ClinGen Strong/Def ≥1 | 380 | 24% | 47% | **76%** |
| IMPC ≥3 KO phenotypes | 351 | 17% | 54% | **83%** |
| Mendelian ≥5 | 213 | 25% | 41% | **75%** |

**Even at strong-evidence Phase 3, 73-85% of attempts still fail.** Multi-line convergence helps but doesn't break the ceiling:

| Convergent evidence | n | Approved | Efficacy fail |
|---|---|---|---|
| C ∧ D ∧ E all high | 1,011 | 14.5% | 35.7% |
| C ∧ D ∧ E ∧ (Mendelian≥5 OR ClinGen) | 422 | 22.0% | 32.2% |

Best possible preclinical profile → **22% approval rate** at Phase 3. The 78% failure rate is the "pathway wrongness" — biology confirms the mechanism works but doesn't confirm the mechanism drives the clinical outcome.

## Robustness — 12 attacks

Every attack we applied to the benchmark. Each row is a challenge to the AUC 0.821 claim; every one has a fix or acknowledged trade-off.

### Attacks fixed

| # | Concern | Fix |
|---|---|---|
| 1 | Loose "any-approval" outcome inflates base rate | `v_target_indication_strict_outcome` — strict per-T-I approval; base rate 5.0% not 23.1% |
| 2 | Post-outcome features leak (n_sponsors, n_programs, max_phase, ot_known_drug, ot_overall) | Removed from feature set |
| 3 | family_approved_count could include post-cutoff approvals | `v_target_family_precedent_by_year` — time-cutoff-aware precedent |
| 4 | Phase 2+ cohort filter = survivorship bias | Also run on Phase 1+ cohort (n=13,821, base rate 2.92%) |
| 5 | Concept drift / temporal generalization | Time-machine backtest: train pre-2019, test post-2019. LogReg AUC 0.80 [0.68, 0.91]; LightGBM 0.54 |
| 6 | Random-split K-fold may leak targets | GroupKFold on `target_id`: Phase 1+ stack drops 1.5pp (0.836 → 0.821) |
| 7 | ML overfitting | Regularized LightGBM with monotonic constraints; report multiple models; unregularized version marked as such |
| 8 | Metric gaming (AUC alone) | Report AUC + Brier + Recall@10% + Precision@10% + RS(top 10%) + ECE. Cross-checks catch tricks |
| 9 | Do features match known biology? | Ablation: removing human genetics drops AUC 19.9pp. HPO direction aligns with RS, but overall coefficient/RS sign alignment is only 55% |
| 12 | Failure-label errors | Haiku + Sonnet dual classification on all 5,510 failed trials with `why_stopped` text; Sonnet-verified for Phase 3 silent kills |

### Attacks acknowledged (not fixed)

- **Cohort composition bias** — target-matched cohort enriched 10× for approved drugs vs raw 82k program universe (ChEMBL/DGIdb select for approved). Claims scoped to "T-I pairs with a target-matched primary drug reaching Phase 1+."
- **Non-CT.gov trials not ingested** — EU-CTR, ChiCTR, JP registries ≈ 20% of global drug development activity.
- **Preclinical / IND-stage kills invisible** — never enter CT.gov.
- **Feature values are current-day for non-precedent features** — Nelson tier, ClinGen, gnomAD, DepMap, Open Targets values are today's snapshots, not cutoff-time. Time-machine tests temporal split but not feature-freeze.
- **`n_dgidb_drugs` and `n_causal_diseases`** — current-day, not time-cutoff. Small residual leakage.
- **Absolute p_approval interpretation is cohort-scoped** — calibrated to 2.92% base rate in Phase 1+ target-matched cohort; not directly comparable to a random drug in the world.

## Files

- `data/leaderboard.csv` — snapshot of all benchmark runs
- `data/approvals.csv` — 544 FDA approvals 2015-2025
- `benchmark/README.md` — benchmark framework methodology + how to plug in a scorer
- `db/README.md` — schema runbook
- `db/QUESTIONS.md` — 25 example SQL queries
- `db/SCHEMA.md` — evidence taxonomy + database design (reference)
- `analyses/final_benchmark.py` — reproduces the headline AUC 0.821
