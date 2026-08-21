# Evidence contract

## Evidence categories and 0-3 strength

Use the most specific applicable category. Do not promote evidence across categories.

| Category | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| Genetic | No reproducible human association | Weak/unresolved GWAS or distant phenotype | Replicated/fine-mapped or coding association | Causal Mendelian/ClinGen strong evidence with matched direction |
| Human PD | No human engagement data | Human PK/exposure only | Biomarker moves in the expected direction | Biomarker plus dose-response and independent replication |
| Mechanistic | Function uncharacterized | General pathway role | Direct binding plus functional mechanism | Deep multi-line mechanism (for example structure, binding, pathway, regulation) |
| Cell | No relevant cell data | Immortalized cell-line pharmacology | Primary human cells respond | iPSC/organoid disease-model rescue, preferably replicated |
| Animal | No efficacy evidence or PK-only | Single rodent model/lab | Replicated or multi-model rodent efficacy | Multi-species, independent replication, or humanized disease-relevant model |

For every phenotype row, report `category`, `score`, and a literal, source-supported evidence statement. Multiple independent rows may describe the same phenotype when evidence categories differ.

Sort report rows in this fixed decision-strength order: `Genetic`, `Human PD`, `Animal`, `Cell`, `Mechanistic`. Include a `Human PD 0/3` row when no human target engagement or pharmacodynamic evidence is verified.

## Direction rules

Use one modulation value from `agonism`, `antagonism`, `activation`, `inhibition`, `loss of function`, `gain of function`, `mixed`, or `unclear`. Separately assign `effect_direction` as `increase`, `decrease`, `mixed`, `unclear`, or `no change`, relative to the named phenotype. State the observed phenotype after that perturbation. Do not infer arrow orientation from modulation: loss of function can increase a phenotype such as obesity, while agonism can decrease a phenotype such as cell death. Do not translate knockout evidence into an antagonist claim without noting developmental compensation and incomplete equivalence.

Reserve `mixed` modulation or effect direction for evidence of opposing effects on the same named phenotype. The existence of GoF and LoF variants causing different syndromes does not make an unrelated focal phenotype bidirectional. When neither perturbation direction is established for that phenotype, use `unclear` plus a dash.

## Claim specificity

Use the most specific verified description that fits the page: exact disease/phenotype, variant or perturbation class, cohort size, species/line, intervention, endpoint, magnitude, timing, and contradiction. Split materially different GoF and LoF phenotypes into separate rows. Avoid umbrella claims such as "pediatric syndromes" when the source supports named conditions and directions.

Human genetics direction must distinguish LoF, GoF, expression-associated variants, and locus associations. `Direction concordant` requires a stated therapeutic hypothesis and evidence that the perturbation direction matches it.

## Source hierarchy

1. Primary experimental paper, official human genetics resource, trial registry, regulatory review/label.
2. Peer-reviewed systematic review or high-quality database with stable provenance.
3. Sponsor disclosure for program ownership/status, paired with independent evidence when available.
4. Patent, conference abstract, press release, or secondary article only when clearly labeled.

Record the cutoff date and verify unstable facts (status, sponsor, trial phase, approval, withdrawal) as of that date.

## Candidate inclusion

Include a candidate only when a source claims direct binding, agonism/antagonism, target degradation, target-directed gene therapy, or target-directed biologic engagement. Put indirect pathway modulators in a separately labeled row only when decision-relevant. State `directness` as `direct`, `claimed direct`, `indirect`, or `disputed`.

Search patent databases for each candidate landscape, including inventions that have not entered clinical trials. Search target symbols and aliases, protein names, modality terms, known inventors, and sponsors. Review composition-of-matter, sequence/biologic, delivery/formulation, and target-specific use claims. A patent mention is insufficient: the claims or worked examples must be materially directed at the target.

Represent one patent family once, using a decision-relevant publication or grant. For a patent-only row, record the publication/grant number, assignee named on the document, priority/publication timing, and verified family/legal status. Label the row `Patent-only; no active program verified` unless an independent current source establishes development. Patent documents are primary evidence for their own claims, examples, filing metadata, and recorded legal events, but not for efficacy, clinical status, present ownership, freedom to operate, or an active program. Use `claimed direct` when directness rests only on a patent. Granted, pending, abandoned, expired, and ceased families may all be included when relevant, with status stated explicitly.

Patent-only inventions compete for the candidate-table row cap by decision relevance. Prefer families that add a distinct modality, chemistry, delivery solution, or safety/translation insight over repetitive claims.

For terminated or withdrawn programs, give the sourced reason. Use `Reason not publicly disclosed` when no reliable reason is available.

For modality strategy, put each real program example and outcome inside the pro or con statement it supports. Do not place examples in a separate column.

## Translational precedent

## Assay definition and model availability

Write each assay as a falsifiable decision rule. Report:

- the exact analyte, image feature, electrophysiology measure, behavior, pathology, or survival endpoint being measured;
- the explicit native-mechanism edge or branch tested;
- the expected positive readout, including direction and normalization/control where possible;
- the expected negative readout and the control that distinguishes inactivity from assay failure;
- the exact cell line, animal strain, allele, genotype, background, and injury/disease induction as applicable.

Verify model availability in official repositories and cite the repository record. Assign `model_availability_score` independently of setup difficulty:

| Score | Availability definition |
|---|---|
| AV3 | Exact line is currently obtainable from a public/commercial repository with a stock/catalog identifier |
| AV2 | Exact line is repository-registered but requires cryorecovery, sperm rederivation, or special access |
| AV1 | Exact line is documented in primary literature, but no current public distribution is verified |
| AV0 | No exact line is verified; it must be assembled or engineered |

Report component resources separately: an orderable parental cell, floxed allele, Cre driver, or disease strain does not make a derived knockout or composite line AV3. Score setup difficulty separately as `D0` routine/off-the-shelf, `D1` published protocol requiring qualification, `D2` specialist breeding/differentiation/aging/surgery/induction, or `D3` de novo engineering and validation.

## Translational precedent

The assay precedent field has a deliberately strict meaning:

- Identify a drug tested with the exact assay, or explicitly the same cell/animal line.
- Identify a Phase 2 or later controlled trial in the same indication.
- Verify that the trial met its prespecified primary efficacy endpoint; merely entering, completing, or showing a subgroup signal is insufficient.
- Cite both preclinical and clinical sources.

If any link is missing, write `None verified` and optionally describe the weaker precedent in `limitations`.

## Audit statuses

Critics return one status per challenged claim:

- `accept`: category, strength, wording, and source support survive independent checking.
- `revise`: claim remains but wording or metadata changes.
- `downgrade`: evidence category/score or certainty is lower.
- `exclude`: claim is unsupported, duplicated, irrelevant, or contradicted beyond repair.
