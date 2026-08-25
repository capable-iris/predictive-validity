# Results

## Headline

**Predicting FDA approval for a specific `(target × indication)` pair from public preclinical evidence:**

Best model: stacked ensemble (LogReg + regularized LightGBM + RandomForest), evaluated with 5-fold GroupKFold on `target_id` (no target appears in both train and test). `nelson_tier` is excluded from all predictive inputs.

| Metric | Value |
|---|---|
| **AUC** | **0.653 [0.622, 0.680]** |
| **RS (top 10%)** | **3.12** |
| **Recall @ top 10%** | 0.257 |
| ECE | 0.001 |
| Cohort | Phase 1+ target-matched T-I pairs, n=13,821 |
| Base rate | 2.92% (404 approved) |

## Nelson-inclusive sensitivity — current database (2026-08-25)

All 15,704 model-adjudicated Nelson facts were imported with exact run/source
provenance. On the unchanged Phase 1+ strict cohort and identical 5-fold
`GroupKFold(target_id)` splits, adding one ordered T0-T3 feature produced:

| Model | Current no-Nelson AUC | With Nelson AUC | Paired ΔAUC (target-bootstrap 95% CI) |
|---|---:|---:|---:|
| Stacked ensemble | 0.754 | **0.775** | **+0.021 [+0.012, +0.032]** |
| LogReg L2 | 0.684 | **0.709** | **+0.026 [+0.013, +0.039]** |

For the stacked model, Nelson increased recall at the top 10% from 0.173 to
0.240 and precision from 0.051 to 0.070; Brier score remained 0.028. These are
sensitivity results, not a replacement for the Nelson-excluded headline.

Coverage is not complete for the headline view: 9,851/13,821 pairs (71.3%)
have adjudicated tiers because the frozen Nelson enumeration and current
outcome view use different target-pair universes. The 3,970 uncovered rows were
forced to T0 so LightGBM could not learn annotation missingness as a separate
signal. Observed approval rates were T0 2.4%, T1 6.3%, T2 7.1%, and T3 12.5%.

The paired current baseline is higher than the 0.653 snapshot below because
many non-Nelson feature rows were populated or refreshed on 2026-08-21/22,
after the 2026-08-20 headline run. The within-run Nelson deltas are valid, but
the current absolute AUCs are not directly comparable to that older snapshot.
Current-day evidence timing also remains a caveat, so Nelson stays excluded
from the canonical predictive feature list.

## Full leaderboard — strict per-T-I outcome

Regenerated Phase 1+ cohort (n=13,821), 5-fold GroupKFold on `target_id`:

| Rank | Scorer | AUC (95% CI) | RS(top 10%) | ECE | R@10% | P@10% |
|---|---|---|---|---|---|---|
| 1 | stacked_final_no_nelson_v1 | **0.653 [0.622, 0.680]** | **3.12** | 0.001 | 0.257 | 0.075 |
| 2 | logreg_final_no_nelson_v1 | 0.643 [0.613, 0.673] | 2.36 | 0.421 | 0.208 | 0.061 |

Historical model, rule-based, LLM, modality and time-machine runs in `data/leaderboard.csv` predate the Nelson exclusion. They remain audit records, not current comparisons, and are omitted here until rerun under the same feature policy.

The corrected same-cohort stacked AUC before exclusion was 0.821. Its fall to 0.653 after removing only the five Nelson one-hot columns demonstrates that selective annotation coverage, rather than tier biology, drove a large part of the previous headline. The earlier 0.825 report is superseded.

## Ablation — what makes the AUC

Full LogReg (strict Phase 2+, n=8,130, 404 approved, 970 targets) = 0.639. Every result uses the same 5-fold `GroupKFold(target_id)` split and excludes Nelson:

| Removed category | Remaining AUC | ΔAUC |
|---|---|---|
| **E. Human PD** | 0.614 | **−2.54pp** |
| **A. Genetics** | 0.620 | **−1.88pp** |
| B. Mechanistic | 0.626 | −1.30pp |
| Context (therapeutic area) | 0.632 | −0.75pp |
| H. Safety | 0.635 | −0.37pp |
| I. Landscape | 0.641 | +0.22pp |
| C. Cell | 0.643 | +0.36pp |
| D. Animal | 0.644 | +0.53pp |

The prior ~19.9pp grouped genetics delta was mostly attributable to the selectively populated Nelson field. After exclusion, genetics retains a modest 1.9pp marginal contribution; human PD has the largest measured contribution at 2.5pp. Removing cell or animal features slightly improves this model's AUC.

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

## Robustness and limitations

The regenerated AUC 0.653 incorporates the strict outcome, held-out-target validation, corrected HPO phenotype definition, and Nelson exclusion.

### Attacks fixed

| # | Concern | Fix |
|---|---|---|
| 1 | Loose "any-approval" outcome inflates base rate | `v_target_indication_strict_outcome` — strict per-T-I approval; base rate 5.0% not 23.1% |
| 2 | Post-outcome or outcome-selected features leak (`n_sponsors`, `n_programs`, phase, known-drug scores, Nelson coverage) | Removed from predictive inputs; Nelson remains stored only for audit/descriptive use |
| 3 | family_approved_count could include post-cutoff approvals | `v_target_family_precedent_by_year` — time-cutoff-aware precedent |
| 4 | Phase 2+ cohort filter = survivorship bias | Headline uses the Phase 1+ cohort (n=13,821, base rate 2.92%) |
| 6 | Random-split K-fold may leak targets | Every current headline and category-ablation result uses `GroupKFold(target_id)` |
| 7 | ML overfitting | Regularized LightGBM with monotonic constraints; report multiple models; unregularized version marked as such |
| 8 | Metric gaming (AUC alone) | Report AUC + Brier + Recall@10% + Precision@10% + RS(top 10%) + ECE. Cross-checks catch tricks |
| 9 | Does one evidence category dominate? | After Nelson exclusion, human PD contributes 2.5pp, genetics 1.9pp, and mechanistic evidence 1.3pp in the grouped Phase 2+ LogReg ablation |
| 12 | Failure-label errors | Haiku + Sonnet dual classification on all 5,510 failed trials with `why_stopped` text; Sonnet-verified for Phase 3 silent kills |

### Attacks acknowledged (not fixed)

- **Cohort composition bias** — target-matched cohort enriched 10× for approved drugs vs raw 82k program universe (ChEMBL/DGIdb select for approved). Claims scoped to "T-I pairs with a target-matched primary drug reaching Phase 1+."
- **Non-CT.gov trials not ingested** — EU-CTR, ChiCTR, JP registries ≈ 20% of global drug development activity.
- **Preclinical / IND-stage kills invisible** — never enter CT.gov.
- **Temporal validation has not yet been regenerated after Nelson exclusion.** Historical time-machine rows in the database and `data/leaderboard.csv` are not current comparisons.
- **Feature values are current-day for non-precedent features** — ClinGen, gnomAD, DepMap and Open Targets values are today's snapshots, not cutoff-time. Nelson is excluded, but the remaining reference features are not fully frozen to each program's trial date.
- **`n_dgidb_drugs` and `n_causal_diseases`** — current-day, not time-cutoff. Small residual leakage.
- **Absolute p_approval interpretation is cohort-scoped** — calibrated to the 2.92% base rate in the Phase 1+ target-matched cohort; not directly comparable to a random drug in the world.

## Files

- `data/benchmark_report.csv` — current regenerated headline and grouped ablation values
- `data/leaderboard.csv` — historical benchmark-run snapshot; older rows predate Nelson exclusion
- `data/approvals.csv` — 544 FDA approvals 2015-2025
- `benchmark/README.md` — benchmark framework methodology + how to plug in a scorer
- `db/README.md` — schema runbook
- `db/QUESTIONS.md` — 25 example SQL queries
- `db/SCHEMA.md` — evidence taxonomy + database design (reference)
- `analyses/final_benchmark.py` — reproduces the headline AUC 0.653
