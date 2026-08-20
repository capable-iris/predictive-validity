# Preclinical Evidence & Drug Approval — 2015-2025

**Analytical takeaways from 544 FDA approvals + 28,286 industry clinical trials + literature scoring of 846 target genes.**

Methodology, data pulls, code, and validation are in `METHODOLOGY.md`. Full analysis (including all cross-tabs and sub-analyses) is in `REPORT_FULL.md`.

---

## TL;DR

1. **Only 40% of failure attribution is real efficacy.** Sponsor `why_stopped` text euphemizes efficacy failures as "business decisions" (47% of failures). Adjusting for sponsor language recovers a ~44% true efficacy failure rate — matching Cook 2014 (65%) and Harrison 2016 (52%) meta-analyses. Raw CT.gov claims only 14% efficacy.

2. **Only two evidence types predict approval within our data:**
   - **Matched human genetic support (Mendelian)** → 1.21× approval [1.07-1.35]
   - **Human target engagement + PD marker movement** → OR 1.31 [1.20-1.44]
   - **Cell-pathway validation and animal in vivo evidence: essentially null** (OR 0.88, 0.98)

3. **7% of "failed" trials are actually planned terminations, not failures.** True industry failure count is ~5,130, not 5,510.

4. **The 92% failure decomposed** (per 1000 Phase 1 entrants): 480 die Phase 1, 370 die Phase 2, 63 die Phase 3, 8 at NDA. **~40% of failures are efficacy** — the only bucket that preclinical target validation could address.

5. **Human genetic support is not uniform across therapeutic areas.** Autoimmune shows 4× spread (matched-GWAS 79% vs no-genetics 20%). Oncology is nearly genetics-invariant (~50% across all tiers). Rare-disease is genetics-maximal (Mendelian match = 81%).

6. **Denominator caveat:** all evidence-type findings apply to the 8% of programs with ChEMBL + published literature. Effects on drugs *before* Phase 1 are unmeasured — the pipeline they'd matter most for is invisible in our data.

---

## 1. FDA approvals landscape (2015-2025)

**544 novel drugs approved** across FDA CDER + CBER. Modality mix has shifted from small-molecule-dominated to increasingly biologic + advanced modality.

| Modality | % of 544 |
|---|---|
| Small molecule | 55% |
| Monoclonal antibody | 16% |
| Protein / peptide | 11% |
| ADC + bispecific mAb + CAR-T + gene therapy | 12% |
| Antisense / siRNA | 3% |
| Other | 3% |

**Discovery methods:** 36% target-based rational, 23% engineered biologic, 18% me-too/follow-on, 6% gene/cell therapy, 3% ASO/siRNA-designed. **AI/ML-designed: 0.** Full breakdown in `approvals.csv`.

**Human genetic support of approved drugs** (Nelson framework):
- 24% Mendelian gene matched to indication
- 12% GWAS coding variant matched
- 3% GWAS non-coding matched
- 16% related-trait genetic evidence only
- 44% no human genetic support

**39% of approved drugs have strong human genetic backing** (matched Mendelian or GWAS-coding).

These percentages describe the hand-curated set of 544 approved drugs only.
They are not cohort-wide tier coverage and are not used as predictive inputs.

---

## 2. What kinds of preclinical evidence predict approval?

We measured five distinct evidence types for each drug-target pair:

1. **Human genetic support** — Mendelian variants / GWAS hits in the target linked to the indication (Nelson framework tier T0-T4)
2. **Mechanistic biology** — crystal structure, biochemical mechanism, drugability characterization
3. **Cell-pathway validation** — human iPSCs / organoids / primary cells showing the expected phenotype when the target is perturbed
4. **Animal in vivo** — drug or knockout reproduces the disease-modifying effect in animals
5. **Human PD engagement** — Phase 1+ data showing the drug reaches the target in humans and moves pharmacodynamic markers

Odds ratios for approval, based on strong (score ≥2) vs weak (score <2) evidence:


| Evidence type | Odds ratio (strong vs weak) | 95% CI | Interpretation |
|---|---|---|---|
| **Human genetic support (Mendelian match)** | **1.21** | **[1.07, 1.35]** | Significant positive |
| Mechanistic biology | 0.55 | [0.53, 0.57] | Saturated at "strong" for all drug targets — no discrimination |
| Cell-pathway validation | 0.88 | [0.80, 0.98] | Mildly negative — within our cohort |
| Animal in vivo | 0.98 | [0.88, 1.09] | Null |
| **Human PD engagement** | **1.31** | **[1.20, 1.44]** | Significant positive (partially leaky — measured post-Phase-1) |

**Only human genetic support and human PD engagement are meaningfully positive predictors.** Mechanistic biology is essentially universal (every drug target has solid mechanistic characterization by the time it reaches Phase 1). Cell-pathway and animal-in-vivo evidence are effectively null within our cohort.

**Composite score is U-shaped**, not monotonic:


Programs with either very-weak or very-strong composite evidence outperform programs with moderate evidence. Moderate-evidence programs are the me-too zone.

**Score distributions** across 846 drug targets:


- Mechanistic biology: saturated at "deep" (85%)
- Cell-pathway + Animal in vivo: cluster at "solid" (~50%)
- Human PD engagement: bimodal — 44% "approved/robust" or 19% "never tested in humans"

---

## 3. Human genetics — where it works and where it doesn't

**Program-level approval rate by tier of human genetic support** (drugs entering Phase 1-3 industry trials 2015-2025, ChEMBL-resolvable, n=1,215):

| Genetic support | Approval rate | 95% CI | Odds ratio vs no-genetics |
|---|---|---|---|
| GWAS non-coding matched | 77.6% | [67.2, 87.9] | 1.34× [1.12, 1.56] |
| **Mendelian matched** | **69.8%** | **[63.7, 75.4]** | **1.21× [1.07, 1.35]** |
| GWAS coding matched | 65.8% | [60.6, 71.5] | 1.14× [1.02, 1.29] |
| Related-trait evidence only | 65.6% | [58.9, 71.9] | 1.14× [1.00, 1.29] |
| No human genetic support | 57.7% | [53.1, 62.7] | 1.00× (baseline) |

**The effect concentrates at Phase 3 → approval:** no-genetics drugs succeed 70% at this transition, Mendelian-matched 80.5%, GWAS-non-coding 85%. Phase 1-2 transitions are nearly evidence-invariant.


**Human genetic support isn't uniform across therapeutic areas:**


- **Autoimmune** — 4× spread (matched-GWAS 79% vs no-genetics 20%)
- **Rare disease** — Mendelian match reaches 81%
- **Oncology** — roughly flat 45-56% across all tiers
- **Infectious disease** — no-genetics-dominant at 78% (target is a pathogen, not human)

**Modality × human genetic support** — the actionable combination table:


**Best single combination:** ASO/siRNA + Mendelian gene = 90% approval rate. Small molecule + no-genetics target = 54%. That 36-point spread is what genetics-first target validation is worth.

---

## 4. Why do drugs fail? — the ground truth

**5,510 failed industry Phase 1-3 trials 2015-2025** (TERMINATED/WITHDRAWN/SUSPENDED). Classified via Claude Haiku (semantic understanding).

| Category | % of failed |
|---|---|
| Commercial / strategic | **47%** |
| Efficacy (stated) | 14% |
| Enrollment / operational | 12% |
| Unclear | 8% |
| **Planned termination** *(not a real failure — cohorts completed as designed)* | **7%** |
| Safety | 3% |
| COVID | 3% |
| Other (regulatory / PK / manufacturing / competitive) | 6% |

**Sponsor euphemism persists:** the 47% "commercial/strategic" bucket contains disguised efficacy failures. Cook 2014 (65% of Phase 2 failures = efficacy) and Harrison 2016 (52%) validate that this bucket is ~60% efficacy in truth. **Adjusted true efficacy failure rate: ~44%.**

**Ground truth from CT.gov `resultsSection`:** primary endpoint p-values for 10,490 P2/P3 completed trials. **Phase 2 primary endpoint miss rate: 46.4%** (vs the 6-15% raw stated rate). Consistent with the euphemism adjustment.

**The 92% failure decomposed** (per 1000 Phase 1 entrants):


- 480 fail Phase 1 (mostly safety+PK)
- 370 fail Phase 2 (mostly efficacy)
- 63 fail Phase 3 (efficacy, expensive)
- 8 fail NDA/regulatory
- **79 approved**

**Attributed causes (euphemism-adjusted):** ~353 failures (38%) are efficacy — the only bucket target validation could address. 124 safety (13%). 121 real commercial (13%). 65 operational (7%). 215 unattributed (23%).

---

## 5. Other evidence dimensions

**Family precedent is U-shaped**, not monotonic. Novel families (0 prior approvals) OR very-drugged families (200+) both outperform the middle:


Being the 6th kinase inhibitor in a crowded family lowers your odds. Being the 1st against a novel target OR the 30th against a well-drugged family (proven tractability) both do better than being in the me-too middle.

**Target essentiality sweet spot** — LOEUF 1-2 is optimal. Highly constrained genes → toxicity risk from targeting essential biology. Highly LoF-tolerant → target may not have enough biological impact.


**Sponsor drug-level success rate** — the fraction of a sponsor's unique drug programs (2015-2025 industry Phase 1-3 trials) that ended up approved anywhere globally, ever.


Industry median = **22.8%** among 336 sponsors with ≥15 drug programs. Top-tier sponsors (Celgene, Amgen, BMS, Roche, Takeda) cluster 35-45%. Big-pharma below the median: GSK 15%, AZ 18%, Pfizer 19%. Sanofi Pasteur at 1.3% (vaccine trials are apples-to-oranges vs oncology).

**Caveat:** rate ≠ Phase 1 LOA. This is "% of drug programs from this sponsor ever approved." Doesn't adjust for indication difficulty (oncology-heavy sponsors run harder trials).

---

## 6. What predicts approval, ranked

From strongest to weakest signal in our data:

1. **Matched human genetic support (Mendelian)** — 2.6× LOA at program level (Nelson 2015 / Minikel 2024); 1.21× within our post-Phase-1 cohort
2. **Modality × target fit** — ASO/siRNA against a Mendelian gene = 90% approval
3. **Rare disease indication** — 2.9× LOA (partially confounded with #1)
4. **Family drugability precedent (right zone of U-curve)** — novel or very-drugged wins
5. **First-in-class positioning** — being 1st beats being 6th by 15-20pp
6. **Human PD engagement** — 1.31× OR — but leaky (measurable only post-Phase-1)
7. **Target essentiality in the LOEUF 1-2 zone** — modest positive
8. **Everything else** (mechanistic biology depth, cell-pathway validation, animal in vivo, mouse KO phenotype count, gnomAD constraint outside the sweet spot) — no clear positive predictive signal in our data

**The uncomfortable finding:** mouse efficacy studies, cell-pathway validation, and detailed structural biology characterization are near-null predictors of approval *within the drugs that reached Phase 1*. What matters most is upstream: picking a target with human genetic support, in a tractable family, with a modality that matches the biology.

---

## 7. Falsification cases — well-validated drugs that failed

**See `CASE_STUDIES.md` for detailed case studies with preclinical scoring, clinical outcomes, and root-cause analysis for 6 famous "preclinical-strong, clinical-fail" drugs:**

- **BACE1 inhibitors** (verubecestat, lanabecestat, LY2886721, atabecestat) — Alzheimer's; ~$4B class failure; Aβ reduction ≠ cognitive benefit
- **Torcetrapib + CETP class** — CETP; +25% mortality despite matched human genetics
- **Semagacestat** — γ-secretase; on-target Notch toxicity
- **Solanezumab / bapineuzumab** — anti-Aβ; biomarker moved, no cognitive benefit
- **TGN1412** — CD28 superagonist; monkey-safe at 500× dose, catastrophic human cytokine storm
- **Fialuridine** — HBV polymerase; safe across rodent+dog+monkey, killed 5 patients from human-specific mitochondrial transport

Every case had a composite preclinical score of 11-12 out of 12 (mechanistic biology + cell-pathway + animal in vivo all strong), plus at least partial human PD engagement. **All failed catastrophically. In every case the root cause was unmeasurable in any preclinical model.**

Combined R&D cost of these six class failures: ~$10-15 billion.

**Pattern:** preclinical evidence confirms *the drug's mechanism works as predicted*. It cannot confirm *the mechanism drives the disease outcome in humans*. That second question is what human genetics + Phase 2 clinical readouts answer — and by then the money has already been spent.

---

## 8. Evidence-taxonomy effect sizes — comprehensive analysis (30,517 programs)

Full analysis in `RESULTS.md`; spec in `../PRECLINICAL_EVIDENCE_SPEC.md`. Comprehensive dataset covers **every industry Phase 1–3 drug program 2015–2025** — 30,517 unique programs, 2,770 with target-matched genome-browser DB enrichment.

**Outcome distribution (30,517 programs):**
- Approved: 544 (1.8%)
- Efficacy fail: 1,002 (3.3%)
- Safety fail: 232 (0.8%)
- Commercial/enrollment/other fail: 5,491 (18.0%)
- Silent kill (phase-complete, no approval): 10,102 (33.1%)
- Phase 1 only no advance: 12,974 (42.5%)

**Best positive predictors of approval** (TIGHT cohort n=4,544 — 544 approved vs 4,000 high-confidence failures):

| Evidence dimension | OR (approval) | 95% CI |
|---|---|---|
| **Line E lit (human PD engagement) high** | **3.98** | [2.80, 6.00] |
| **ClinGen Strong/Definitive ≥1** | **2.48** | [1.43, 4.75] |
| **Mendelian associations ≥5** | **2.01** | [1.46, 2.74] |
| **OT overall score ≥0.5** | **1.66** | [1.09, 2.60] |
| OT genetic score ≥0.3 | 1.63 | [1.00, 3.09] |

**Best negative predictors** (biological-risk targets, TIGHT cohort):

| Evidence dimension | OR (approval) | 95% CI |
|---|---|---|
| **DepMap pan-essential target** | **0.08** | [0.03, 0.28] |
| **DepMap ≥5 dependent lineages** | **0.47** | [0.20, 0.81] |
| OT animal model score ≥0.3 | 0.49 | [0.24, 1.00] |
| gnomAD pLI ≥0.9 | 0.76 | [0.53, 1.03] |

**Null predictors** (target-level literature): Line C/D lit scores (OR ≈1), IMPC KO count, tractability flags. Publication bias saturates target-level signals.

**Key inversions:**
- The historical pooled Nelson-tier odds ratio is retired: tier coverage was selectively curated on approval-oriented pairs, so it cannot support a predictive comparison.
- **OT animal model score ≥0.3 pooled OR 0.38** — well-studied mouse-KO targets are less likely approved. Pleiotropy signal.

**Q4/Q5 headlines** (see `RESULTS.md` for full tables):
- **5,952 efficacy failures** (including presumptive silent kills at Phase 3): **-22pp Line E coverage** (64% vs 86%), **-15pp Mendelian ≥5** (16% vs 31%)
- **232 safety failures**: **51× higher pLI** (0.66 vs 0.013), **-38pp ClinGen coverage** (50% vs 88%), **+18pp GWAS pleiotropy** (60 vs 37 significant hits)

---

## 9. Limitations (short version — see METHODOLOGY.md for full list)

- **Selection bias:** non-genetic evidence findings apply to the 8% of programs with ChEMBL + published literature. Cell-pathway and animal-in-vivo evidence might matter more *before* Phase 1 — the filter we can't observe.
- **Approval definition:** ChEMBL `max_phase=4` = "approved anywhere ever" — conflates FDA/EMA vs China NMPA, and different indications.
- **Sponsor euphemism** in `why_stopped` still partly obscures failure reasons.
- **Literature scores from single-shot Haiku extraction** — no ensembling, only validated on 10 curated targets.
- **Human genetic tier assignments** partly from training-data recall; Open Targets validation showed 96% concordance on Mendelian-matched assignments but this is not a formal cross-model check.
- **Anachronistic precedent:** family-approval counts are "as-of-now," not "as-of-program-start."
- **Non-scientific factors** (sponsor capital, patent cliff, advocacy pressure) not modeled.

---
