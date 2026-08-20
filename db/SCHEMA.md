# Preclinical Evidence → Clinical Efficacy: Comprehensive Spec

**Core question:** how do different kinds of preclinical evidence predict clinical efficacy, and what are the effect sizes?

**Scope:** every industry-sponsored Phase 1–3 drug/biological clinical trial 2015–2025 (28,286 trials), joined against ~1,700 unique drug programs, ~1,050 unique gene targets, and every relevant preclinical evidence source.

**Data sources available:** genome-browser Neon DB (24+ tables, 9 external sources ingested) + our session's JSONL products + PubMed direct queries.

---

## Part 1 — Taxonomy of preclinical evidence

Grouped by decision-relevant category. Every dimension has (a) definition, (b) data source, (c) tier definition (0-3 or categorical), (d) known predictive-power benchmark.

### Category A — Human genetic evidence (per target × indication)

Answers: **is the target causally implicated in the disease by human genetics?**

| Dimension | Definition | Source | Tiers |
|---|---|---|---|
| A1. **Mendelian evidence** | LoF/GoF variants in the gene cause a monogenic form of the disease, direction concordant with drug MoA | Orphanet + OMIM (`mendelian_associations` in gb) | 0=none / 1=distant / 2=related / 3=causal-matched |
| A2. **GWAS coding variant** | Missense/LoF variant reaches genome-wide significance for the trait | GWAS Catalog (`gwas_associations` in gb) | 0=none / 1=weak / 2=coding-matched / 3=coding + effect direction |
| A3. **GWAS non-coding L2G** | Non-coding hit with confident locus-to-gene assignment | Open Targets L2G | 0/1/2/3 |
| A4. **Gene burden test** | Rare-variant burden significant for indication | UK Biobank / gnomAD ExWAS | 0/1/2/3 |
| A5. **Direction concordance** | Drug MoA direction (agonist vs antagonist) matches human LoF/GoF direction | Derived | boolean |
| A6. **ClinGen validity** | Expert curation of gene-disease causality | ClinGen (`clingen_validity` in gb) | Definitive / Strong / Moderate / Limited / Disputed / Refuted |
| A7. **Human phenotype breadth** | Number of distinct, positively observed terms in the HPO Phenotypic abnormality branch (`HP:0000118`) linked to the gene through human gene-disease annotations; a target-level, indication-agnostic pleiotropy proxy | Pinned official HPO release + `gene_phenotypes` in gb (OMIM/ORPHA annotations) | Count; inheritance, onset/course modifiers, and explicit zero-frequency annotations are excluded |

**Composite:** Nelson tier T0-T4. The stored tier remains available for audit and descriptive analysis, but is temporarily excluded from predictive models because coverage in this database was selectively curated on approval-oriented pairs. Reintroduction requires uniform, indication-specific, pre-outcome computation and held-out-target validation.

### Category B — Mechanistic biology (per target, some drug-level)

Answers: **do we understand the target well enough to drug it precisely?**

| Dimension | Definition | Source | Tiers |
|---|---|---|---|
| B1. **Structural biology** | Crystal structures of target ± drug complex | PDB (`protein_structure` in gb, ready but not ingested) | Count of PDB IDs, count with ligand |
| B2. **Binding characterization** | Kd, Ki, kon/koff published | ChEMBL activity | Full / partial / none |
| B3. **Selectivity panel** | Off-target activity across related targets | ChEMBL activity (multiple targets), DGIdb | 0/1/2/3 |
| B4. **Enzyme kinetics** | For enzymes: catalytic mechanism, allostery | UniProt EC + literature | 0/1/2/3 |
| B5. **Isoform / splice variant coverage** | Which isoform is targeted; consequences of hitting others | UniProt + genome-browser targets.ip_type | qualitative |
| B6. **PTM regulation** | Phosphorylation / methylation / etc. regulating target | PhosphoSitePlus / literature | 0/1/2/3 |

**Composite:** structural-biology score. Predictive effect: **saturated at high for approved drug targets** (weak marginal signal per current data).

### Category C — Cell-pathway validation (per target + per drug)

Answers: **do human-relevant cells respond to perturbation as predicted for the disease?**

| Dimension | Definition | Source | Tiers |
|---|---|---|---|
| C1. **Cell-line pharmacology** | Drug tested in cancer / immortalized cell lines with IC50 | ChEMBL activity | 0=none / 1=basic / 2=multiple / 3=full panel |
| C2. **iPSC-derived models** | Drug or perturbation tested in iPSC-derived disease-relevant cells | PubMed extraction | 0/1/2/3 |
| C3. **Organoid models** | 3D organoid recapitulation of disease phenotype ± drug rescue | PubMed extraction | 0/1/2/3 |
| C4. **Primary human cells** | Drug tested in patient-derived primary cells (blood, biopsy, resected tissue) | PubMed extraction | 0/1/2/3 |
| C5. **CRISPR essentiality** | For oncology: does gene KO kill the relevant cancer cell line? | DepMap (`gene_essentiality` in gb, ~21M scores) | Chronos score |
| C6. **Perturbation-rescue** | Direct target modulation rescues disease phenotype in human cells | PubMed extraction | 0/1/2/3 |
| C7. **Pathway convergence** | Multiple perturbations in target's pathway produce same phenotype | STRING PPI (`protein_interactions` in gb) + literature | derived |

**Composite:** cell-pathway validation score. Predictive effect: **currently null at target-level (OR 0.90 [0.83, 1.00])**. Drug-level TBD (pipeline running).

### Category D — Animal in vivo (per target + per drug)

Answers: **does the drug or KO reproduce the disease-modifying effect in animals?**

| Dimension | Definition | Source | Tiers |
|---|---|---|---|
| D1. **Mouse KO phenotype** | IMPC / MGI: does gene KO give disease-relevant phenotype? | IMPC Solr + MGI | 0/1/2/3, phenotype relevance |
| D2. **Rodent drug efficacy** | Drug tested in rodent disease model, effect size | PubMed extraction | 0/1/2/3 |
| D3. **Non-rodent efficacy** | Dog, monkey, non-human primate efficacy | PubMed extraction | 0/1/2/3 |
| D4. **Model relevance** | Disease model recapitulates human disease (not just symptomatic proxy) | Literature curation | 0/1/2/3 |
| D5. **Independent replication** | Multiple labs reproduce the finding | PubMed extraction | 0/1/2 |
| D6. **Humanized models** | Humanized mouse with human target expressed | Literature | binary |
| D7. **Dose-response coherence** | PK/PD in animal predictive of expected human dose | Literature | 0/1/2/3 |
| D8. **Chronic dosing safety** | Long-term (30-day+) dosing in animals without emergent tox | Regulatory summaries | 0/1/2/3 |
| D9. **Preclinical tox flags (multi)** | Cardiovascular, hepatic, hematological, neurological, genotoxic, reprotoxic | Regulatory + literature | per-organ 0-3 |

**Composite:** animal in vivo score. Predictive effect: **null at target-level (OR 1.01 [0.93, 1.09])**. Drug-level TBD.

### Category E — Human PD engagement (drug-specific, Phase 1+)

Answers: **does the drug engage its target in humans and move pharmacodynamic markers as predicted?**

| Dimension | Definition | Source | Tiers |
|---|---|---|---|
| E1. **PD biomarker movement** | Downstream biomarker changes in direction and magnitude predicted | CT.gov results + PubMed | 0/1/2/3 |
| E2. **Target-engagement (direct)** | PET / receptor occupancy imaging showing drug at target | Literature | 0/1/2/3 |
| E3. **Dose-response coherence** | Human dose-response matches preclinical PK/PD | Literature | 0/1/2/3 |
| E4. **Cross-species PK translation** | Preclinical allometric predictions accurate in humans | Literature | 0/1/2 |
| E5. **Immunogenicity** (for biologics) | ADA formation, neutralizing antibodies | CT.gov + literature | 0/1/2/3 |
| E6. **Chronic-dose safety window** | Human tolerability at exposures needed for efficacy | Regulatory + literature | 0/1/2/3 |
| E7. **Companion Dx / biomarker strategy** | Predictive biomarker for responders exists and validated | `therapy_biomarkers` in gb (PharmGKB) | boolean + role |

### Category F — Human clinical efficacy (outcome — post-hoc)

Answers: **did the drug meet its primary endpoint?**

Directly derived from CT.gov `resultsSection` + FDA labels + published trials. Includes:

- F1. Phase 2 primary endpoint met (Y/N/mixed)
- F2. Phase 3 primary endpoint met (Y/N/mixed)
- F3. Effect size vs comparator
- F4. Subgroup consistency
- F5. Independent replication (multiple confirmatory trials)
- F6. Long-term durability

### Category G — Pharmacology / DMPK / chemistry

Answers: **is the drug drug-like enough to work in humans?**

| Dimension | Source | Notes |
|---|---|---|
| G1. Ro5 compliance | ChEMBL molecule properties | Small molecules only |
| G2. Half-life (h) | Literature + FDA labels | — |
| G3. Oral bioavailability | Literature + FDA labels | — |
| G4. Protein binding | Literature | — |
| G5. CYP profile | Literature | Metabolism liabilities |
| G6. Route of administration | ChEMBL / FDA labels | — |

Predictive effect: covered 4-10% of failures per Waring 2015.

### Category H — Safety (multi-dimensional)

Answers: **will hitting this target cause harm we can't dose around?**

| Dimension | Source | Notes |
|---|---|---|
| H1. LoF safety (LOEUF) | gnomAD (`gene_constraint` in gb) | See rubric §lof_safety |
| H2. Essentiality (DepMap) | DepMap (`gene_essentiality_summary`) | Chronos score |
| H3. On-target adverse event class | SIDER (`adverse_events` in gb) | 8 MedDRA-PT SOC keyword classes |
| H4. Off-target liability panel | ChEMBL selectivity data | — |
| H5. Cardiovascular (hERG) | Literature + regulatory | — |
| H6. Hepatotoxicity (DILI) | Literature + regulatory | — |
| H7. Immunotoxicity | Literature + regulatory | — |
| H8. Reproductive tox | Literature + regulatory | — |

### Category I — Landscape / commercial (ancillary)

- I1. Prior approvals against same target (family precedent)
- I2. First-in-class flag
- I3. Competitive intensity (# active programs on same target)
- I4. Regulatory pathway used (orphan, breakthrough, accelerated)
- I5. Indication difficulty (baseline LOA by therapeutic area)

---

## Part 2 — The 5 core questions and how to answer each

### Q1: What are the tiers of *in vivo* evidence and to what extent do they predict clinical efficacy?

**Tiers** (Category D):
- **Tier 0:** No animal data (never tested in vivo, or PK-only)
- **Tier 1:** Rodent efficacy in one disease model
- **Tier 2:** Rodent efficacy + non-rodent species (dog / primate)
- **Tier 3:** Multi-species + independent replication + humanized model
- **Kill flag:** In vivo tox signals in 2+ organ systems

**Analysis:**
- Population: 5,510 failed + 2,120 completed-approved programs
- For each program: score in vivo tier per drug-specific PubMed extraction
- Compute P(approved | tier) with 95% bootstrap CI
- Compare against baseline P(approved) = ~15%
- Effect size = odds ratio tier N vs tier 0
- Stratify by therapeutic area (in vivo evidence more predictive in some TAs)

**Current best data:** Line D scores from `literature_scores.jsonl` (target-level, OR 1.01 [0.93, 1.09] — null). Drug-level pipeline running.

**Extension needed:** join genome-browser `target_evidence.animal_model_score` (Open Targets phenodigm) for validation of Tier 3 assignments.

### Q2: What are the tiers of *in vitro* evidence and to what extent do they predict clinical efficacy?

**Tiers** (Category C):
- **Tier 0:** No cell data (target ID only)
- **Tier 1:** Cell-line pharmacology (IC50 in immortalized lines)
- **Tier 2:** Primary human cells respond
- **Tier 3:** iPSC / organoid disease-model rescue demonstrated
- **Tier 3+:** Cross-lab replication with matched-patient cells

**Analysis:** Same shape as Q1 with C-dimension scores.

**Current best data:** Line C scores from `literature_scores.jsonl`. OR 0.90 [0.83, 1.00] at target-level. Drug-level pipeline running.

**Extension needed:** DepMap essentiality (`gene_essentiality` in gb) as sub-signal for cancer targets. STRING PPI convergence for pathway-level Tier 3.

### Q3: What are all classes of preclinical evidence compiled before clinical trials?

**Answer:** the 8 categories A–H above. Complete taxonomy in Part 1. Every dimension has:
- Data source
- Tier definition
- Public availability assessment
- Coverage rate in our current data

**Report deliverable:** matrix of (dimension × drug) showing evidence coverage. Sparse across most drugs — reflects public-data limitations.

### Q4: Within efficacy failures, what types of evidence did or did not exist?

**Analysis:**
- Filter to trials classified `efficacy` (Haiku or Sonnet, high-confidence)
- For each drug in this set, compute evidence coverage across A–H
- Compare against evidence coverage for approved drugs matched on therapeutic area
- **Key question:** what evidence dimensions systematically differ between efficacy-failures and approvals?

**Expected findings:**
- Efficacy failures likely have low or missing Category A (human genetics) — the strongest predictor
- Similar Category B/C/D coverage across both (i.e., preclinical work is equally rigorous whether or not it translates)
- Category E (PD engagement) may distinguish (approved drugs = target-engaged in humans; efficacy failures = often achieved engagement but no benefit)

**Deliverable:** side-by-side coverage matrix + odds-ratio-like heatmap showing which evidence types matter for avoiding efficacy failure.

### Q5: Within safety failures, what safety evidence did or did not exist?

**Analysis:**
- Filter to trials classified `safety` (Haiku or Sonnet, high-confidence)
- For each drug: extract Category H dimensions from all sources
- Compare against approved-drug safety profiles matched on target class

**Expected findings:**
- Safety failures likely have deficits in Category H4 (off-target liability panel) — many preclinical safety programs use limited off-target screens
- Species-specific safety (H3-H8 in humans absent from H1-H7 in animals) → common cause per TGN1412, fialuridine case studies
- Cardiovascular (H5) and hepatotoxicity (H6) most common causes per FAERS analyses

**Deliverable:** categorical breakdown of safety failure modes with evidence-gap analysis.

---

## Part 3 — Data source unification map

For each evidence category, the primary source table (in genome-browser or our JSONL):

| Category | Genome-browser table | Our JSONL | External (PubMed / API) |
|---|---|---|---|
| A. Human genetics | `mendelian_associations`, `gwas_associations`, `clingen_validity`, `target_evidence.genetic_score` | `nelson_tiers_batch_*.csv`, `opentargets_associations.jsonl`, `tier_validation.csv` | Open Targets GraphQL |
| B. Mechanistic biology | `targets.ip_type`, `targets.family`, `targets.tractability_*`, `target_evidence.literature_score` | `chembl_mechanisms.json`, `chembl_targets.json` | PDB REST, PubMed |
| C. Cell-pathway | `gene_essentiality` (DepMap), `single_cell_expression` (HPA), `protein_interactions` (STRING) | `literature_scores.jsonl` (Line C, target-level), `drug_evidence.jsonl` (Line C, drug-level, in progress) | PubMed |
| D. Animal in vivo | `target_evidence.animal_model_score` (Open Targets phenodigm) | `gene_impc_summary.csv` (67K IMPC records), `literature_scores.jsonl` (Line D), `drug_evidence.jsonl` (Line D) | IMPC Solr, PubMed |
| E. Human PD engagement | `trials.result_pmids`, `therapy_biomarkers` | `trials_results.jsonl`, `literature_scores.jsonl` (Line E), `drug_evidence.jsonl` (Line E) | PubMed |
| F. Clinical efficacy | `trials`, `approvals` | `approvals.csv`, `trials_industry_drug.csv`, `trials_results.jsonl`, `why_stopped_haiku.jsonl`, `why_stopped_sonnet.jsonl` | CT.gov API |
| G. Chemistry / DMPK | `targets.tractability_sm`, `therapies.route_of_administration` | `chembl_drugs.json` | ChEMBL |
| H. Safety | `gene_constraint` (gnomAD), `gene_essentiality_summary` (DepMap), `adverse_events` (SIDER), `target_evidence.somatic_score` | — | ChEMBL activity panels |
| I. Landscape | `approvals`, `sponsors`, `trials` | `approvals.csv`, `sponsors.csv`, `program_master.csv` | — |

---

## Part 4 — Effect-size analysis specification

For each Q1-Q5, the analytical output should be:

1. **Coverage table** — % of drugs with data for each dimension
2. **Effect-size table** — for each dimension, OR for approval (Q1-Q3) or contribution to failure mode (Q4-Q5), with 95% CI
3. **Interaction analysis** — where two dimensions co-occur, joint OR
4. **Therapeutic-area stratification** — do effect sizes vary by TA?
5. **Case examples** — 5-10 named examples per finding (drug + why it exemplifies the finding)

Statistical methodology:
- Bootstrap CIs on all rates (already implemented)
- Correction for multiple comparisons on effect-size tests
- Adjust for cohort composition (some sponsors overweight in oncology; correct for TA imbalance)

---

## Part 5 — Deliverables

### D1. Master evidence dataset
- Table `drug_evidence_full` — one row per (drug × indication) with all A-I dimensions
- Sources:
  - Genome-browser Neon DB (via SQL)
  - Our JSONL products (via CSV export)
  - PubMed direct extraction (for missing drug-specific data)
- Persistence: CSV export + optional Neon insertion for genome-browser integration

### D2. Coverage matrix
- Rows: 8 categories (A-H, I ancillary)
- Columns: 3 slices (approved, efficacy-failed, safety-failed)
- Cells: % coverage of each dimension

### D3. Effect-size report
- One table per question Q1-Q5
- One-page summary with headline numbers
- Full table with bootstrap CIs
- Interaction plots

### D4. Case studies (extension of `CASE_STUDIES.md`)
- 5 more high-signal examples covering:
  - Efficacy failures with strong Category A (e.g., ARN-509 → androgen receptor, well-validated but tox)
  - Efficacy failures with strong Category C/D (BACE1 already covered)
  - Safety failures with weak Category H4 (TGN1412 already covered)
  - Successful drugs with weak Category A (many antibiotics, imaging agents)
  - Programs that succeeded on Category E alone

### D5. Report integration
- Rewrite REPORT.md §2 with drug-specific evidence-line odds ratios
- Add new section §6 "Evidence-type effect sizes"
- Update `MODEL_SPEC.md` with the full 60+ feature list from the taxonomy

---

## Part 6 — Publication bias handling

Every finding must be explicitly caveated for:
1. **Preclinical publication bias** — negative animal/cell data for failed drugs largely absent
2. **Selection bias** — analysis restricted to drugs that reached Phase 1
3. **Sponsor euphemism** — commercial/strategic bucket contains ~60% disguised efficacy failures per Cook 2014
4. **Circular LLM knowledge** — Haiku knows if a drug was approved; scores may be biased toward positive for approved drugs

Where possible, use structured database sources (genome-browser tables) rather than LLM extraction to avoid circular bias.

---

## Part 7 — Integration with genome-browser

The genome-browser DB provides ~80% of the evidence dimensions we need. Our contributions add:
- Drug-specific evidence extraction (Category C/D/E at drug level, not just target level)
- Failure classification with sponsor-euphemism correction (Category F)
- Full failed-trial taxonomy with confidence-weighted labels
- Multi-model verification (Haiku + Sonnet agreement)

Recommended: our new artifacts feed into genome-browser as:
- New table `drug_preclinical_evidence` (per-drug scoring)
- New columns on `trials`: `failure_reason_llm`, `failure_reason_publication`, `failure_confidence`
- New view `approval_predictor_features` — flat table joining all evidence dimensions for ML

---

## Part 8 — What "fully complete" looks like

The task is complete when we can answer, with a citation and effect size:

1. **"If a drug has strong human genetic support (Nelson T1), how much does that increase its odds of approval vs Nelson T0?"**
   - Answer: current data says 1.21× [1.07, 1.35] at trial-cohort level, ~2.6× at program level (Minikel 2024)

2. **"If a drug has strong drug-specific animal-model efficacy (Tier 3), how much does that predict approval?"**
   - Pending: `drug_evidence.jsonl` pipeline output

3. **"Of drugs that failed for efficacy, what fraction had strong Category C (cell-pathway) evidence?"**
   - Pending: coverage-matrix analysis on Sonnet-verified failure classifications

4. **"For approved drugs with no human genetic support (T0), what other evidence carried them through?"**
   - Partial answer in current report §9.4 (family precedent, immunology, cytotoxics)

5. **"Which evidence dimension has the highest odds ratio for approval?"**
   - Current: human genetic support (Nelson T1) and human PD engagement (Line E). Full ranking pending drug-level evidence completion.

The task is complete when all five have quantitative answers with CIs, effect-size comparisons, and case-study anchors.


---

# Database Schema Design

# `preclin.*` Schema Design — Preclinical Evidence Analysis Layer

**Goal:** move the drug-outcome + preclinical-evidence analysis into Neon Postgres so that every data point has a **single source of truth**, every analysis is a straightforward SQL query, and the schema stays **as simple as possible while covering all necessary data**.

---

## Design principles (in priority order)

1. **Single source of truth per fact.** Each atomic claim (a score, a classification, an outcome) lives in exactly one row. Everything else is a view.
2. **Reuse `public.*` where authoritative.** `public.targets`, `public.trials`, `public.sponsors` cover our needs and are already ingested. Don't duplicate.
3. **Extend `public.*` where incomplete.** `public.approvals` (166 rows) and `public.diseases` (167 rows) are too sparse — we ingest our own approvals + indications.
4. **Long-form facts, wide-form views.** Extending evidence dimensions later means adding a row, not a column. Analyses read materialized views.
5. **Version every LLM output.** `source_model + source_version + extracted_at` on every LLM-produced row. Multiple extractions coexist; views pick the latest by default.
6. **No hidden joins.** Every FK explicit, every view definition < 50 lines. Anyone reading the schema can trace where a number came from.

---

## Entity model

```
        public.targets ────────────┐         public.sponsors
             │  (existing, 41K)    │              │  (existing, 42K)
             │                     │              │
             ▼                     │              ▼
     preclin.evidence_target ──────┼──────  preclin.drug
             │  (facts)            │              │  (extends public.therapies + trial-only drugs)
             ▼                     │              │
     preclin.evidence_target_indication          ▼
                                                 preclin.program
                                                        │  (drug × indication × sponsor — our analytical unit)
                                                        │
                     ┌──────────────────┬────────────────┼──────────────┐
                     ▼                  ▼                ▼              ▼
              preclin.program_    preclin.evidence_   preclin.       preclin.
              trial (junction)    drug (facts)        program_       classification
                     │                                outcome        (LLM outputs)
                     ▼
              public.trials  (existing, 441K)

              preclin.indication ─── links to public.diseases when a match exists
```

**Six new tables. Three reference existing `public.*` tables. Everything else is views.**

---

## Table specs

### Reference tables (existing `public.*` — read-only, use FKs to)
- `public.targets` — gene identity (`target_id`)
- `public.trials` — CT.gov trials (`nct_id`)
- `public.sponsors` — sponsor identity (`sponsor_id`)
- `public.gene_essentiality_summary`, `public.gene_constraint`, `public.clingen_validity`, `public.mendelian_associations`, `public.gwas_associations`, `public.target_evidence`, `public.adverse_events` — read for enrichment; no writes

### New: `preclin.drug`
Canonical drug identity. Superset of `public.therapies`.

```sql
CREATE TABLE preclin.drug (
  drug_id            SERIAL PRIMARY KEY,
  normalized_name    TEXT NOT NULL UNIQUE,          -- e.g., 'pembrolizumab'
  display_name       TEXT NOT NULL,                 -- e.g., 'Pembrolizumab'
  therapy_id         INTEGER REFERENCES public.therapies(id),  -- null if trial-only
  chembl_id          TEXT,
  drugbank_id        TEXT,
  modality           TEXT,                          -- small_molecule, mab, adc, car_t, etc.
  is_placebo         BOOLEAN DEFAULT FALSE,
  is_combination     BOOLEAN DEFAULT FALSE,
  resolved_via       TEXT,  -- 'public_therapy' | 'chembl_bulk' | 'llm_haiku' | 'llm_sonnet_verified' | 'unresolved'
  resolved_at        TIMESTAMPTZ,
  created_at         TIMESTAMPTZ DEFAULT now()
);
```

**Rationale:** one drug_id per canonical drug. `resolved_via` records provenance of the target-mapping (needed for selection-bias analysis).

### New: `preclin.drug_target`
Junction. A drug can have multiple targets (combinations, secondary targets).

```sql
CREATE TABLE preclin.drug_target (
  drug_id       INTEGER NOT NULL REFERENCES preclin.drug(drug_id),
  target_id     INTEGER NOT NULL REFERENCES public.targets(id),
  role          TEXT NOT NULL,   -- 'primary' | 'secondary' | 'off_target' | 'component_of_combo'
  mechanism     TEXT,            -- 'agonist' | 'antagonist' | 'inhibitor' | 'degrader' | 'other'
  source        TEXT NOT NULL,   -- 'chembl' | 'llm_sonnet' | 'therapy_targets' | 'llm_haiku'
  confidence    TEXT,            -- 'high' | 'medium' | 'low'
  citation_pmid TEXT,
  extracted_at  TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (drug_id, target_id, role, source)
);
```

**Rationale:** multi-target support without WIDE nullable columns. Multiple sources of the same (drug, target) can coexist; latest-confidence rules in views.

### New: `preclin.indication`
Canonical indications. Superset of `public.diseases` (which is too small).

```sql
CREATE TABLE preclin.indication (
  indication_id      SERIAL PRIMARY KEY,
  normalized_name    TEXT NOT NULL UNIQUE,  -- lowercased, punctuation-stripped
  display_name       TEXT NOT NULL,
  disease_id         INTEGER REFERENCES public.diseases(id),  -- when curated match exists
  mondo_id           TEXT,     -- ontology anchor
  therapeutic_area   TEXT,     -- 'oncology' | 'neuro' | 'autoimmune' | ...
  ct_gov_conditions  TEXT[],   -- variant strings seen in CT.gov
  created_at         TIMESTAMPTZ DEFAULT now()
);
```

### New: `preclin.program`
**The core analytical unit.** One row per (drug × indication × sponsor) developed together.

```sql
CREATE TABLE preclin.program (
  program_id      SERIAL PRIMARY KEY,
  drug_id         INTEGER NOT NULL REFERENCES preclin.drug(drug_id),
  indication_id   INTEGER NOT NULL REFERENCES preclin.indication(indication_id),
  sponsor_id      INTEGER REFERENCES public.sponsors(id),
  first_trial_date DATE,
  last_trial_date  DATE,
  highest_phase    INTEGER,   -- 0-4
  n_trials         INTEGER,
  created_at       TIMESTAMPTZ DEFAULT now(),
  UNIQUE (drug_id, indication_id, sponsor_id)
);
```

**Rationale:** program is the level at which "approval," "efficacy failure," etc., have meaning. A drug developed in oncology + autoimmune is two programs.

### New: `preclin.program_trial`
Junction (program × trial).

```sql
CREATE TABLE preclin.program_trial (
  program_id  INTEGER NOT NULL REFERENCES preclin.program(program_id),
  nct_id      TEXT NOT NULL REFERENCES public.trials(nct_id),
  PRIMARY KEY (program_id, nct_id)
);
```

### New: `preclin.program_outcome`
Computed rollup — the "did this program succeed or fail" atom.

```sql
CREATE TABLE preclin.program_outcome (
  program_id       INTEGER PRIMARY KEY REFERENCES preclin.program(program_id),
  outcome          TEXT NOT NULL,  -- see enum below
  outcome_broad    TEXT NOT NULL,  -- coarsened: 'approved' | 'efficacy_fail' | 'safety_fail' | 'silent_kill' | 'in_dev' | 'planned'
  confidence       TEXT NOT NULL,  -- 'high' | 'medium' | 'low'
  approval_id      INTEGER REFERENCES preclin.approval(approval_id),
  failure_reasons  JSONB,  -- {"efficacy": 2, "safety": 0, "commercial": 1}
  computed_at      TIMESTAMPTZ DEFAULT now()
);
```

Outcome enum: `approved`, `efficacy_fail`, `safety_fail`, `commercial_fail`, `enrollment_fail`, `other_fail`, `phase_complete_no_approval`, `phase1_only`, `in_development`, `planned_termination`, `unknown`.

### New: `preclin.approval`
Our approvals table. Extends `public.approvals` (166 rows too sparse).

```sql
CREATE TABLE preclin.approval (
  approval_id      SERIAL PRIMARY KEY,
  drug_id          INTEGER NOT NULL REFERENCES preclin.drug(drug_id),
  indication_id    INTEGER REFERENCES preclin.indication(indication_id),
  agency           TEXT NOT NULL,   -- 'FDA_CDER' | 'FDA_CBER' | 'EMA' | ...
  approval_date    DATE,
  approval_year    INTEGER,
  nelson_tier      TEXT,            -- 'T0' | 'T1' | 'T2' | 'T3' | 'T4'
  first_in_class   BOOLEAN,
  orphan           BOOLEAN,
  breakthrough     BOOLEAN,
  accelerated      BOOLEAN,
  priority_review  BOOLEAN,
  source_url       TEXT,
  public_approval_id INTEGER REFERENCES public.approvals(id),  -- link when overlap
  created_at       TIMESTAMPTZ DEFAULT now()
);
```

### New: `preclin.evidence_score` (the fact table)
**One row per (subject × dimension × source × extraction).**

```sql
CREATE TABLE preclin.evidence_score (
  evidence_id     BIGSERIAL PRIMARY KEY,
  subject_type    TEXT NOT NULL,  -- 'target' | 'target_indication' | 'drug' | 'program'
  subject_id      INTEGER NOT NULL,
  dimension       TEXT NOT NULL,  -- e.g., 'line_c_lit' | 'line_e_lit' | 'nelson_tier' | 'depmap_pan_essential'
  category        TEXT NOT NULL,  -- 'A_genetics' | 'B_mechanistic' | 'C_cell' | 'D_animal' | 'E_pd' | 'H_safety' | 'I_landscape'
  value_numeric   DOUBLE PRECISION,
  value_text      TEXT,
  value_boolean   BOOLEAN,
  source          TEXT NOT NULL,  -- 'pubmed_haiku' | 'depmap' | 'gnomad' | 'clingen' | 'impc' | ...
  source_version  TEXT,           -- '2026-01' | 'v1.2'
  confidence      TEXT,           -- 'high' | 'medium' | 'low'
  citation_pmids  TEXT[],
  extracted_at    TIMESTAMPTZ DEFAULT now(),
  extracted_by    TEXT,           -- 'claude-haiku' | 'claude-sonnet' | 'manual'
  UNIQUE (subject_type, subject_id, dimension, source, source_version)
);
CREATE INDEX ON preclin.evidence_score (subject_type, subject_id);
CREATE INDEX ON preclin.evidence_score (dimension, source);
```

**Rationale:** the ONE table that holds every evidence claim. Adding a new evidence type = new dimension string, no schema change. Multiple sources per (subject, dimension) coexist; views resolve.

### New: `preclin.classification` (the classifier output table)
Failure reasons + silent-kill verifications + drug-target resolutions.

```sql
CREATE TABLE preclin.classification (
  classification_id  BIGSERIAL PRIMARY KEY,
  subject_type       TEXT NOT NULL,   -- 'trial' | 'program' | 'drug'
  subject_key        TEXT NOT NULL,   -- nct_id, program_id-as-text, drug_id-as-text
  classifier_task    TEXT NOT NULL,   -- 'why_stopped' | 'silent_kill_verify' | 'target_resolution'
  category           TEXT NOT NULL,   -- e.g., 'efficacy' | 'safety' | 'commercial_strategic'
  confidence         TEXT,
  rationale          TEXT,
  citation_pmids     TEXT[],
  classifier_model   TEXT NOT NULL,   -- 'claude-haiku' | 'claude-sonnet' | 'regex_v1'
  classifier_version TEXT,
  extracted_at       TIMESTAMPTZ DEFAULT now(),
  UNIQUE (subject_type, subject_key, classifier_task, classifier_model, classifier_version)
);
CREATE INDEX ON preclin.classification (subject_type, subject_key);
```

**Rationale:** allows Haiku + Sonnet classifications to coexist per NCT id; disagreements are queryable directly.

---

## Views (analysis surface)

### `preclin.v_drug_target` — latest resolved target per drug
Picks the highest-confidence resolution per drug.

### `preclin.v_program_evidence_wide` — the flat master
The `drug_evidence_master_v2_broad.csv` equivalent. Pivots evidence_score into wide columns for one row per program.

```sql
CREATE VIEW preclin.v_program_evidence_wide AS
SELECT p.program_id, d.normalized_name AS drug, i.display_name AS indication,
       t.symbol AS target_symbol, po.outcome, po.outcome_broad,
       MAX(CASE WHEN es.dimension = 'line_c_lit'  THEN es.value_numeric END) AS line_c_lit,
       MAX(CASE WHEN es.dimension = 'line_d_lit'  THEN es.value_numeric END) AS line_d_lit,
       MAX(CASE WHEN es.dimension = 'line_e_lit'  THEN es.value_numeric END) AS line_e_lit,
       -- ... etc for all 20 dims
       ...
FROM preclin.program p
JOIN preclin.drug d ON d.drug_id = p.drug_id
JOIN preclin.indication i ON i.indication_id = p.indication_id
JOIN preclin.program_outcome po ON po.program_id = p.program_id
LEFT JOIN preclin.v_drug_target dt ON dt.drug_id = p.drug_id
LEFT JOIN public.targets t ON t.id = dt.target_id
LEFT JOIN preclin.evidence_score es ON
    (es.subject_type = 'target' AND es.subject_id = t.id) OR
    (es.subject_type = 'drug' AND es.subject_id = p.drug_id)
GROUP BY p.program_id, d.normalized_name, i.display_name, t.symbol, po.outcome, po.outcome_broad;
```

### `preclin.v_pathway_wrongness`
The Phase 3 pathway-fail-rate analysis. One query, ~30 lines.

```sql
CREATE VIEW preclin.v_pathway_wrongness AS
WITH ph3 AS (
  SELECT * FROM preclin.v_program_evidence_wide WHERE highest_phase >= 3
)
SELECT
  'Line C (cell lit)' AS dimension,
  COUNT(*) FILTER (WHERE line_c_lit >= 2) AS high_ev_n,
  COUNT(*) FILTER (WHERE line_c_lit >= 2 AND outcome_broad = 'approved') AS high_approved,
  COUNT(*) FILTER (WHERE line_c_lit >= 2 AND outcome_broad IN ('efficacy_fail', 'presumptive_efficacy_fail_ph3')) AS high_eff_fail,
  ROUND(100.0 * COUNT(*) FILTER (WHERE line_c_lit >= 2 AND outcome_broad != 'approved') /
         NULLIF(COUNT(*) FILTER (WHERE line_c_lit >= 2), 0), 1) AS high_fail_pct
FROM ph3
UNION ALL
-- one row per evidence dimension
SELECT 'Line E (human PD lit)', ...;
```

### `preclin.v_effect_sizes_tight`, `preclin.v_effect_sizes_broad`
OR + CI per dimension (bootstrap CI computed offline; the view has n_high_approved / n_high_failed / etc. — CIs added by a nightly Python job to a separate table `preclin.effect_size_snapshot`).

### `preclin.v_failure_taxonomy`
Distribution of failure reasons across all classified trials.

---

## Migration map (JSONL → schema)

| Current artifact | Rows | Target table | Notes |
|---|---|---|---|
| `approvals.csv` | 544 | `preclin.approval` + `preclin.drug` + `preclin.indication` | Extend `public.approvals` (only 166 rows) |
| `drug_master_lookup.csv` | 24,887 | `preclin.drug` + `preclin.drug_target` | Source='chembl_bulk'; `resolved_via='chembl_bulk'` |
| `resolved_targets.jsonl` | 963 | `preclin.drug_target` | Source='llm_haiku', `resolved_via='llm_haiku'` |
| `verified_targets.jsonl` | 391 | `preclin.drug_target` | Source='llm_sonnet_verified' (overrides Haiku) |
| `unresolved_targets_sonnet.jsonl` | growing | `preclin.drug_target` | Source='llm_sonnet' |
| `literature_scores.jsonl` | 1,095 | `preclin.evidence_score` | subject_type='target', dimension=`line_{b,c,d,e}_lit` |
| `drug_evidence.jsonl` | 81 | `preclin.evidence_score` | subject_type='drug', dimension=`drug_*` (cell_efficacy, rodent_efficacy, etc.) |
| `nelson_tiers_batch_*.csv` | 395 | `preclin.evidence_score` | subject_type='target_indication', dimension='nelson_tier' |
| `gene_impc_summary.csv` | 8,429 | `preclin.evidence_score` | subject_type='target', dimension='impc_n_phenotypes' |
| `family_precedent.csv` | 537 | `preclin.evidence_score` | subject_type='target', dimension='family_approved_count' |
| `opentargets_associations.jsonl` | 1,052 | `preclin.evidence_score` | subject_type='target_indication', dimension='ot_association' |
| `why_stopped_haiku.jsonl` | 5,510 | `preclin.classification` | classifier_task='why_stopped', model='claude-haiku' |
| `why_stopped_sonnet.jsonl` | ~7,000 | `preclin.classification` | model='claude-sonnet' |
| `silent_kill_verified.jsonl` | growing | `preclin.classification` | classifier_task='silent_kill_verify' |
| `trials_industry_drug.csv` | 28,301 | (already in `public.trials`) — filter via view |

**Everything else** (`programs_with_lit_scores.csv`, `drug_evidence_master_v2*.csv`, `program_master.csv`) is a *derived* artifact — becomes a materialized view, not a stored table.

---

## Example: pathway-wrongness query today vs after

**Today (Python, 200 lines):**
```python
python3 pathway_wrongness.py
# reads drug_evidence_master_v2_broad.csv (7 MB)
# python pandas-like joins
# outputs PATHWAY_WRONGNESS.md
```

**After migration (SQL, 5 lines):**
```sql
SELECT * FROM preclin.v_pathway_wrongness ORDER BY dimension;
-- returns pathway-fail rate per evidence dimension in one query
-- reproducible: same query gives same answer forever
-- discoverable: someone browsing schema can find + rerun
```

---

## Design decisions I made and why

**Long-form `evidence_score` fact table** — a single wide `drug_evidence_full` table (67 columns and growing) makes ALTER TABLE painful and hides the provenance of each column. Long-form: adding a new dimension = one INSERT, no migration. Views handle the wide-form for analysis.

**Separate `preclin.approval` instead of extending `public.approvals`** — genome-browser's is 166 rows and appears agency-specific. Ours is broader (544 across CDER + CBER + planned to include EMA). Link with `public_approval_id` FK for the 166 that overlap.

**`preclin.indication` instead of using `public.diseases`** — public.diseases has 167 rows and is a curated shortlist for the dashboard. Our indications come from arbitrary CT.gov `conditions` strings (~5,000 unique). We can still link via `disease_id` FK when a curated match exists.

**Program = (drug × indication × sponsor)** — this is the level at which "approved" or "failed for efficacy" is a valid statement. A drug developed for two indications is two programs with independent outcomes.

**`resolved_via` on `drug`** — critical for selection-bias analysis. Someone can filter to "only look at drugs resolved via public.therapies" (ChEMBL-catalogued, systemic winner bias) vs "resolved via LLM" (fills in the failed-drug gap).

**Version every LLM output** — `source_version` + `extracted_at`. When we re-run Haiku next month with a better prompt, old rows stay, new rows overlay, view picks max(extracted_at). Never lose a decision.

**Full audit trail** — every fact has `extracted_by` (which agent/model), `citation_pmids` (source publications), `confidence`. If someone asks "why does this drug have Line C=3", the SQL shows the exact PMIDs and the extractor.

---

## Not doing (deliberately)

**No wide flat "master" table on disk.** The wide form is a materialized view rebuilt nightly. This kills the temptation to write straight to a wide table and skip provenance.

**No `preclin.trials` copy.** `public.trials` (441K rows) is authoritative. We just filter via a view.

**No hierarchical dimension enum.** Category ('A_genetics', 'C_cell', etc.) is a text column, not a foreign key to a dimension table. Simple > normalized-to-death.

**No incremental refresh of materialized views.** Nightly full rebuild. If we scale to millions of programs (won't happen), revisit.

**Current evidence projection plus immutable run history.** `evidence_score`
keeps one current value per subject/dimension/source/version. Every audited LLM
run stores its produced value in `llm_run_evidence_score.fact_snapshot`, so a
later rerun can update the current projection without rewriting history.

**Trial-linked PubMed abstracts are stored after migration 10.**
`preclin.source_document` holds immutable, SHA-256-versioned ClinicalTrials.gov
records and PubMed abstracts; `preclin.source_document_subject` links them to
trials, targets, drugs, and target-indication pairs. Target-literature abstracts
may be loaded from the scorer cache, but are stored in this same document table.
`preclin.v_trial_source_latest` exposes the verbatim registry
`why_stopped_text` and PubMed `abstract_text` directly, while retaining the
complete upstream JSON/XML payload behind both.

**Exact LLM inputs are retained for new runs.** `preclin.llm_run`
stores the system prompt, user prompt, raw response, parameters, usage, and
content hashes. `preclin.llm_run_source` records which immutable documents—and
which truncated excerpts—were model inputs. `preclin.llm_run_evidence_score`
connects one extraction call to all evidence facts it produced and preserves
the run-produced value independently of the mutable current projection. Existing
historical classifications are marked as legacy rows whose exact prompts cannot
be reconstructed.

---

## Open questions before I implement

1. **Should `preclin.program` be by unique (drug, indication, sponsor) or (drug, indication) with sponsor as attribute?** I proposed 3-tuple. Rationale: same drug developed by 2 sponsors for same indication is 2 programs (they had independent outcomes). Alternative: 2-tuple (drug, indication) with primary_sponsor, list of co-sponsors. Simpler but loses info.

2. **How to handle drugs approved via ChEMBL max_phase=4 outside FDA?** Our current "approved anywhere ever" definition. Should this be a separate outcome tag (`approved_ex_us`)? Or one `approved` bucket?

3. **Does `preclin.evidence_score` become huge?** Estimate: 30k programs × 15 dimensions × 2-3 sources = ~1.2M rows. Well within Neon's capacity (compare `public.gwas_associations` at 1M). Postgres handles it fine.

4. **Do we want `preclin.raw_ingestion_proposal` matching your Iris pattern?** Your `MEMORY.md` mentions propose-then-promote 24h cadence. If yes, I add a proposal table + promotion trigger. Otherwise direct-write.

5. **Migration cutover strategy.** Two options:
   - **Big-bang**: write ingest script, drop schema, reload, cut over analyses to SQL. 4-6 hours, all-or-nothing.
   - **Dual-write**: keep JSONL as the write path, add nightly job that syncs to DB. Analyses can run either way during transition.
   
   I recommend big-bang given data volume is modest and this is one-off setup.

---

## Estimated size

- 7 new tables
- 4-5 materialized views (rebuilt nightly)
- ~1.5M rows total across all preclin tables (dominated by `evidence_score`)
- ~500 MB storage on Neon (well within any tier)

---

## Recommended next steps

1. **You review this design** — key questions in §"Open questions" above
2. **I write the DDL** — one SQL file, ~250 lines, creates all tables + indexes + FKs
3. **I write the ingest script** — one Python file, ~500 lines, loads every JSONL/CSV listed above
4. **I write the view definitions** — one SQL file, ~200 lines, creates the 4-5 analysis views
5. **I rewrite `pathway_wrongness.py` and `effect_sizes_final.py` as SQL queries** so we have proof this works
6. **I document in a runbook** — how to add a new evidence dimension, how to promote an experimental classifier, etc.

Total: ~6 hours end-to-end.

---

## Alternative I considered and rejected

**Just use JSONL + DuckDB.** DuckDB reads CSV/JSON natively, no schema needed. Would work today. Rejected because: (a) not shared with Iris / other apps, (b) not accessible from claude.ai/code or phone, (c) no single source of truth for evidence — each file remains authoritative for its slice.

**Push everything to `public.*` in genome-browser.** Rejected because: (a) affects dashboard schema, (b) genome-browser's tables are curated for target-search, not drug-outcome analysis. Our workload is different shape.
