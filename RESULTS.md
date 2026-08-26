# Results

## Headline

**Predicting FDA approval for a specific `(target × indication)` pair from public preclinical evidence:**

Canonical calibrated model: stacked ensemble (LogReg + regularized LightGBM +
RandomForest), evaluated with 5-fold GroupKFold on `target_id` (no target
appears in both train and test). `nelson_tier` is excluded from all headline
predictive inputs. LogReg has a statistically indistinguishable AUC of 0.571
but poor calibration.

| Metric | Value |
|---|---|
| **AUC** | **0.570 [0.515, 0.620]** |
| **RS (top 10%)** | **1.46** |
| **Recall @ top 10%** | 0.140 |
| ECE | 0.002 |
| Cohort | Phase 1+ approval-independent consensus-target T-I pairs, n=10,685 |
| Base rate | 3.14% (336 approved; 792 targets) |

Individual model intervals now use 1,000 target-cluster bootstrap resamples
(seed 42), preserving all target-indication rows whenever a target is sampled.
The earlier row-bootstrap intervals are retained only in historical v4 runs.

### Matched random controls

On the identical 10,685 rows, 2,000 seeded controls assigned one random rank to
each of the 792 targets, with random within-target tie breaking. Median random
AUC was 0.501 and the 95% random-null range was 0.459–0.545. The no-Nelson
stacked AUC of 0.570 was matched or exceeded in 2/2,000 iterations (empirical
p=0.0015); both Nelson models exceeded all 2,000 draws (corrected empirical
p=0.0005). Random-control median recall at the top 10% was 0.101, precision was
0.0318, and RS was 1.014. A separate constant-prevalence control has AUC 0.500,
Brier 0.03046, and ECE 0.

## Nelson-inclusive sensitivity — current database (2026-08-26)

The earlier analysis coerced absent adjudications to T0 and later overcorrected
by restricting the cohort to drugs whose best-priority target rows all agreed.
The final correction recovers mappings only when source priority, confidence,
and independent-source corroboration identify one unique target. Explicit
`fda_approval` mappings are excluded from this process: they changed 27 target
assignments and 50 mapping memberships and inflated the Phase 1+ approved count
from 336 to 382. Exact strongest ties remain unresolved rather than guessed.

Nelson was then adjudicated for every genuinely missing pair in the complete,
outcome-blind clinical universe. Coverage is 12,142/12,142 clinical pairs and
10,685/10,685 Phase 1+ benchmark rows. Benchmark tiers are T0 9,176; T1 775;
T2 134; T3 600. Missing or invalid tiers now fail the run instead of being
imputed. On identical ordered rows and 5-fold `GroupKFold(target_id)` splits,
adding one ordered T0-T3 feature produced:

| Model | Current no-Nelson AUC | With Nelson AUC | Paired ΔAUC (target-bootstrap 95% CI) |
|---|---:|---:|---:|
| Stacked ensemble | 0.570 | **0.602** | **+0.032 [+0.007, +0.059]** |
| LogReg L2 | 0.571 | **0.612** | **+0.041 [+0.019, +0.064]** |

For the stacked model, Nelson increased recall at the top 10% from 0.140 to
0.244 and precision from 0.044 to 0.077; RS increased from 1.46 to 2.91, while
Brier score changed from 0.03041 to 0.03027. Coverage leakage, false-T0
imputation, and approval-derived target mapping are removed. Current-day
evidence timing remains a material caveat, including approval-associated growth
in GWAS and ClinGen records documented below, so Nelson stays excluded from the
canonical predictive feature list.

## Missing-feature policy correction

The previous log transform silently converted every missing count to zero
before model-pipeline imputation. The corrected policy distinguishes semantics:

- absence from complete relationship snapshots (Mendelian, ClinGen, GWAS,
  qualifying HPO associations, Open Targets component scores,
  high-confidence PPI/Reactome/GO, and DGIdb) is explicit zero;
- missing assay or coverage measurements (including gnomAD, DepMap, tissue,
  single-cell, literature, and IMPC fields) remain `NaN` and are median-
  imputed using only the training fold;
- synthetic HPO and Open Targets somatic rows from the historical blanket-zero
  backfill are removed; zero recorded evidence is derived at feature assembly
  without pretending that the upstream source reported a literal zero;
- the 100%-missing `ot_l2g_score_max` is removed;
- current-day `family_approved_count` and `gene_approved_count` are removed
  because correct historical values require a pre-trial approval cutoff.

No missingness indicators are added because source coverage can itself reflect
post-outcome research attention.

## IMPC DR24 correction — feature and predictive effect (2026-08-26)

Repeated 2025 imports created four identical IMPC rows per target. Migration 23
removed 23,493 duplicates and retained one row for each of 7,831 targets; this
did not change model inputs because the wide view already used `MAX`. The DR24
audit then examined all 444 Phase 1+ targets absent from the 2025 summary. The
numeric feature now contains six unambiguous new positive counts and 26 zeros
supported by one mouse mapping, no significant MP term, and at least 13
successful homozygous procedures. All ambiguous, partial, unphenotyped, and
unresolved states remain unknown. The exact transition was 353 synthetic zeros
to unknown, six synthetic zeros to positive, six unknowns to supported zero, 20
synthetic zeros retained as supported zero, and 59 unknowns retained.

On the unchanged 10,685-pair cohort and folds, the descriptive before/after
effect was small relative to sampling uncertainty:

| Model | Before IMPC correction | DR24 corrected | ΔAUC |
|---|---:|---:|---:|
| Stacked, no Nelson | 0.568 | **0.570** | +0.0025 |
| LogReg, no Nelson | 0.572 | 0.571 | −0.0006 |
| Stacked + Nelson | 0.605 | 0.602 | −0.0025 |
| LogReg + Nelson | 0.614 | 0.612 | −0.0017 |

The database retains both run generations under distinct scorer names. Target-
level changes are in `data/impc_dr24_feature_changes.csv`; full metric changes
are in `data/impc_dr24_predictive_effect.csv`.

## Approval-associated research amplification — GWAS and ClinGen (2026-08-25)

To quantify preferential research focus after target validation, a target-level
event study compared first-approved targets with targets that were not yet
approved. Treatment is the earliest molecule approval attached to a direct,
approved ChEMBL mechanism for an exactly gene-symbol-matched human
single-protein target. The local approval table was not used for treatment
timing because its 2015 start would misclassify older validated targets as newly
approved. The analysis covers 2016–2025, uses 2019–2022 approval cohorts, takes
year −1 as the reference, and summarizes years +1 to +3; the approval year is
excluded from the summary because only annual approval timing is available.
There are 34 treated targets and 512 clinical targets with known ChEMBL mapping
status. Inference uses 5,000 target-cluster bootstrap resamples (seed 42).

| Outcome | Primary: not-yet-approved controls | Never-approved sensitivity | Pre-trend p (primary) | Treated targets with ≥1 post record |
|---|---:|---:|---:|---:|
| Distinct GWAS studies, cumulative years +1 to +3 | **+8.05/target [−0.03, +18.16]** | +8.13 [+0.23, +18.14] | 0.826 | 34/34 |
| ClinGen classifications, cumulative years +1 to +3 | **+0.199/target [+0.047, +0.378]** | +0.193 [+0.033, +0.377] | 0.178 | 7/34 |

For GWAS, treated targets had 30.85 observed studies over the three post years
versus an estimated 22.80 without approval, a point difference of 35%. This is
not robust evidence of a proportional effect: the mean post-period log1p effect
was 0.111 [−0.157, 0.382], and the primary raw-count interval includes zero.
Removing one treated target at a time leaves the cumulative raw estimate between
+6.46 and +9.37 studies, so no single target creates the full point estimate.

ClinGen is too sparse for a stable relative multiplier: its counterfactual is
near zero and only seven treated targets have a classification in years +1 to
+3. The absolute association remains positive in the bootstrap, and its
leave-one-treated-target-out range is +0.145 to +0.207 classifications, but it
should not be interpreted as a precise ecosystem-wide effect.

These are approval-associated changes, not causal estimates. Approval is
selected by prior biology and investment, publication dates lag research
initiation, and non-rejection of the two measured pre-period coefficients does
not establish parallel trends. The result supports treating current-day GWAS
and ClinGen volume as potentially post-outcome-influenced, but does not justify
a universal numeric deflation factor. The estimator follows cohort-time
difference-in-differences building blocks rather than a two-way fixed-effects
event study ([Callaway and Sant’Anna, 2021](https://doi.org/10.1016/j.jeconom.2020.12.001)); versioned treatment provenance is stored in normalized PostgreSQL tables from [ChEMBL Data Web Services](https://chembl.gitbook.io/chembl-interface-documentation/web-services/chembl-data-web-services).

## Full leaderboard — strict per-T-I outcome

Regenerated approval-independent consensus-target Phase 1+ cohort (n=10,685),
5-fold GroupKFold on `target_id`:

| Rank | Scorer | AUC (95% CI) | RS(top 10%) | ECE | R@10% | P@10% |
|---|---|---|---|---|---|---|
| 1 | logreg_final_no_nelson_target_bootstrap_v5 | **0.571 [0.520, 0.616]** | 1.76 | 0.442 | 0.164 | 0.051 |
| 2 | stacked_final_no_nelson_target_bootstrap_v5 | 0.570 [0.515, 0.620] | **1.46** | **0.002** | 0.140 | 0.044 |

Historical model, rule-based, LLM, modality and time-machine runs in `data/leaderboard.csv` predate the Nelson exclusion. They remain audit records, not current comparisons, and are omitted here until rerun under the same feature policy.

Earlier 13,821- and 8,090-row results are superseded because arbitrary or
over-conservative target attribution changed cohort membership. The paired
Nelson result above is the valid comparison on the corrected cohort.

## Ablation — what makes the AUC

Historical pre-correction analysis: Full LogReg (strict Phase 2+, n=8,130,
404 approved, 970 targets) = 0.639. These rows predate the consensus-target and
missing-feature corrections and must be regenerated before being compared with
the current headline:

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

The regenerated AUC 0.570 incorporates the strict outcome, held-out-target
validation, corrected HPO phenotype definition, approval-independent consensus
target attribution, corrected missing-feature semantics, the audited IMPC DR24
update, and Nelson exclusion.

### Attacks fixed

| # | Concern | Fix |
|---|---|---|
| 1 | Loose "any-approval" outcome inflates base rate | `v_target_indication_strict_outcome` — strict per-T-I approval; base rate 5.0% not 23.1% |
| 2 | Post-outcome or outcome-selected features leak (`n_sponsors`, `n_programs`, phase, known-drug scores, approval counts, Nelson coverage) | Removed from headline predictive inputs; Nelson remains a separate sensitivity |
| 3 | Missing values were indiscriminately converted to zero | Structural absence is zero; unknown measurements remain `NaN` for training-fold-only imputation |
| 4 | Phase 2+ cohort filter = survivorship bias | Headline uses the Phase 1+ cohort (n=10,685, base rate 3.14%) |
| 5 | Equally preferred or FDA-derived drug-target mappings affected attribution | Approval-independent source/confidence/corroboration consensus; unresolved strongest ties are excluded |
| 6 | Random-split K-fold may leak targets | Every current headline and category-ablation result uses `GroupKFold(target_id)` |
| 7 | ML overfitting | Regularized LightGBM with monotonic constraints; report multiple models; unregularized version marked as such |
| 8 | Metric gaming (AUC alone) | Report AUC + Brier + Recall@10% + Precision@10% + RS(top 10%) + ECE. Cross-checks catch tricks |
| 9 | Does one evidence category dominate? | After Nelson exclusion, human PD contributes 2.5pp, genetics 1.9pp, and mechanistic evidence 1.3pp in the grouped Phase 2+ LogReg ablation |
| 10 | Row bootstrap treats correlated target-indication rows as independent | Current v5 intervals resample all 792 targets as clusters (1,000 iterations, seed 42) |
| 11 | No matched chance control | Added 2,000 seeded random target rankings plus a constant-prevalence calibration control |
| 12 | Failure-label errors | Haiku + Sonnet dual classification on all 5,510 failed trials with `why_stopped` text; Sonnet-verified for Phase 3 silent kills |

### Attacks acknowledged (not fixed)

- **Cohort composition bias** — target-matched cohort enriched 10× for approved drugs vs raw 82k program universe (ChEMBL/DGIdb select for approved). Claims scoped to "T-I pairs with a target-matched primary drug reaching Phase 1+."
- **Non-CT.gov trials not ingested** — EU-CTR, ChiCTR, JP registries ≈ 20% of global drug development activity.
- **Preclinical / IND-stage kills invisible** — never enter CT.gov.
- **Temporal validation has not yet been regenerated after Nelson exclusion.** Historical time-machine rows in the database and `data/leaderboard.csv` are not current comparisons.
- **Feature values are current-day for non-precedent features** — ClinGen, gnomAD, DepMap and Open Targets values are today's snapshots, not cutoff-time. Nelson is excluded, but the remaining reference features are not fully frozen to each program's trial date.
- **`n_dgidb_drugs` and `n_causal_diseases`** — current-day, not time-cutoff. Small residual leakage.
- **Absolute p_approval interpretation is cohort-scoped** — calibrated to the 3.14% base rate in the Phase 1+ approval-independent consensus-target cohort; not directly comparable to a random drug in the world.

## Files

- `data/benchmark_report.csv` — current regenerated headline and grouped ablation values
- `data/leaderboard.csv` — historical benchmark-run snapshot; older rows predate Nelson exclusion
- `data/approvals.csv` — 544 FDA approvals 2015-2025
- `analyses/approval-evidence-effect/data/approval_research_event_study.csv` — dynamic GWAS/ClinGen estimates and intervals
- `analyses/approval-evidence-effect/data/approval_research_event_study_summary.json` — full method audit, summaries, and influence checks
- `benchmark/README.md` — benchmark framework methodology + how to plug in a scorer
- `db/README.md` — schema runbook
- `db/QUESTIONS.md` — 25 example SQL queries
- `db/SCHEMA.md` — evidence taxonomy + database design (reference)
- `analyses/final_benchmark.py` — reproduces the headline stacked AUC 0.570
- `analyses/approval-evidence-effect/approval_research_event_study.py` — reproduces the approval-associated GWAS/ClinGen analysis
- `analyses/random_control_benchmark.py` — reproduces the seeded matched random-target and prevalence controls
- `data/random_control_target_bootstrap_v5.csv` — complete random-null ranges and empirical p-values
- `db/25_chembl_target_approval_history.sql` — normalized ChEMBL release, target-mapping, and molecule-approval tables
- `analyses/approval-evidence-effect/fetch_chembl_target_approval_history.py` — refreshes those ChEMBL tables directly
