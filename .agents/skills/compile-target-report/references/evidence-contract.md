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

The phenotype inventory is target-wide and indication-agnostic. The focal indication must not be an inclusion criterion. Search all material source-supported domains that could change target direction, safety, modality, biomarker strategy, or indication choice, including development, behavior, metabolism, organ physiology, reproduction, immunity, oncology, and adverse phenotypes as applicable. A report for one indication should therefore retain well-supported phenotypes from other diseases or physiological systems.

For every phenotype row, report `category`, `score`, and a literal, source-supported evidence statement. Multiple independent rows may describe the same phenotype when evidence categories differ. Merge findings only when perturbation class, phenotype direction, category, and tissue context align; keep distinct syndromes, opposing directions, and safety liabilities separate. Within the row cap, represent every material phenotype domain rather than selecting only findings supportive of the focal indication. If breadth exceeds the cap, combine only biologically coherent outcomes and surface any materially omitted domain in limitations.

Sort report rows in this fixed decision-strength order: `Genetic`, `Human PD`, `Animal`, `Cell`, `Mechanistic`. Include a `Human PD 0/3` row only when no human target engagement or pharmacodynamic evidence is verified across any indication.

## Phenotype-to-pipeline reconciliation

Before final row selection, compare the phenotype inventory with every candidate indication, human provocation or PD outcome, program termination driver, and major safety or modality liability. A material human phenotype must not disappear because its evidence is ligand-mediated, shared across receptor family members, pathway-level, disputed, or contradicted by a negative target-specific trial.

When target attribution is unresolved:

- name the phenotype explicitly and say which ligand, pathway, receptor family, or indirect perturbation produced it;
- distinguish `pathway implicated` from `target causal` and do not convert the association into a therapeutic direction;
- use `unclear` modulation and the weakest justified category/score when a phenotype row is still decision-relevant;
- retain negative target-specific clinical evidence as an attribution boundary rather than treating it as proof that the phenotype is irrelevant;
- if the row cap forces omission, name the phenotype and attribution dispute in `assessment.limitations`.

The reconciliation is complete only when every clinical-program indication and every major human safety or provocation signal is represented in the phenotype table, explicitly excluded with a reason, or surfaced in limitations. This is a coverage check, not permission to inflate indirect pathway evidence into direct target evidence.

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

## Prospective modality reasoning

Keep the modality strategy distinct from the candidate landscape. The candidate table records what has been attempted; modality strategy asks what could rationally be attempted. Use two passes:

1. **First-principles generation:** before reviewing target-specific programs, infer the intervention requirements from the desired modulation direction, target topology and compartment, pocket or interface geometry, endogenous ligand/substrate pharmacophore, catalytic versus scaffolding role, tissue and BBB access, turnover, homolog selectivity, required duration, and therapeutic-window constraints. Generate plausible modalities from those properties, including untried approaches.
2. **Evidence-based reality check:** then search direct target programs, structurally or mechanistically analogous targets, modality-class precedent, patents, delivery data, and failures. Use these findings to revise rank and uncertainty, not to retroactively limit the generated option set.

Represent each modality case with one or more sourced premises and at least one explicit inference. Use only these basis labels:

- `Observed - target-specific`: direct structural, biophysical, pharmacologic, localization, expression, genetic, or program evidence for the target.
- `Observed - analogous target/class`: evidence from a close structural/mechanistic analog or a relevant modality and delivery class. Name why the analog is relevant.
- `First-principles inference`: a conclusion derived from cited premises, not an observed fact.

Sources must support the premises; they need not literally state the inference. Do not treat absence of a target-specific program as evidence against physical plausibility, and do not treat physical plausibility as validation. For every modality, record the strongest evidence boundary, the most credible failure mode, and the least expensive experiment that can reject the concept. Rank by mechanistic fit, physical feasibility, delivery, controllability, safety/selectivity, biomarkerability, and development burden rather than by program count.

### Patent differentiation screen

For every ranked modality, inspect representative independent claims, examples, family members, jurisdictions, and verified legal status for the closest patent families. Identify which claim dimensions appear to overlap the proposal: composition or sequence, binding site or epitope, mechanism or signaling bias, target-specific use, delivery or formulation, route, dose, and indication. Then report:

- `Low`: no active, materially overlapping claims were identified in the scoped search; this is still not legal clearance.
- `Moderate`: one or more active families overlap important features, but a materially distinct chemotype/sequence, site, profile, delivery, or use appears technically plausible.
- `High`: multiple or apparently broad active families overlap the core concept, so substantial technical differentiation or licensing may be needed.
- `Unknown`: claim scope, family status, jurisdictional coverage, or proposal definition is too incomplete for a responsible estimate.

State the searched overlap and the concrete differentiation levers. Cite the patent documents supporting the assessment. A patent search is not a freedom-to-operate opinion: do not infer non-infringement, validity, enforceability, present ownership, or legal clearance. Recommend a jurisdiction-specific claim chart by patent counsel before relying on a `Low`, `Moderate`, or `High` planning label.

## Translational precedent

## Assay definition and model availability

Build an assay cascade in iteration order. The first in vitro row should be the simplest practical cell-based target-engagement or target-proximal assay, followed by a functional cell assay and then a more complex disease-relevant assay only when each adds a distinct decision. Direct engagement includes cellular occupancy/binding, target abundance or localization, or a proximal biochemical or functional event that demonstrates modulation at the target. A distal phenotype alone is not target engagement. If direct cellular engagement is not technically credible, state the gap and use the closest proximal pharmacodynamic readout without relabeling it.

Prefer short-turnaround, quantitative, concentration-response-capable assays using wild-type or off-the-shelf cells when they answer the immediate question. Do not make engineered cells, primary differentiation, iPSC models, organoids, or multicellular systems the default first experiment solely because they are more disease-like.

For every target, explicitly assess whether wild-type animals can yield a useful exposure-response, pharmacodynamic, physiological, behavioral, or tolerability signal. Prefer that route for early iteration when it is interpretable, even if it is not a disease model. If wild-type animals are unlikely to be informative, record the biological reason before escalating to knockout, transgenic, induced, surgical, or disease models. Include the useful wild-type assay in the report; retain a negative wild-type feasibility assessment in the research record and surface it as a limitation when it materially changes the development plan.

Coarse, low-burden in vivo endpoints are valid when their resolution matches the decision. Home-cage activity or actigraphy-derived rest/activity may support an early sleep-like signal, for example, but must be labeled indirect and cannot establish sleep stages, sleep architecture, or definitive sleep without EEG/EMG or another validated sleep measurement.

Write each assay as a falsifiable decision rule. Report:

- the exact analyte, image feature, electrophysiology measure, behavior, pathology, or survival endpoint being measured;
- the explicit native-mechanism edge or branch tested;
- the expected positive readout, including direction and normalization/control where possible;
- the expected negative readout and the control that distinguishes inactivity from assay failure;
- the exact cell line, animal strain, allele, genotype, background, and injury/disease induction as applicable.

Order rows by practical iteration value: target engagement or proximal pharmacology first, then simple functional response, then complex or disease-specific validation. Within the same role, prefer faster and less operationally burdensome models. Complexity is justified only by a distinct question that simpler assays cannot answer.

For every in vivo row, report species-to-human conservation in three parts:

- **Receptor/target:** orthology and isoforms; conservation of key ligand-, drug-, antibody-, or catalytic-contact residues; endogenous-ligand pharmacology; and direct cross-species activity of the proposed modality when available.
- **Pathway/phenotype:** coupling and downstream signaling; relevant tissue and cell-type expression; circuit or organ physiology; and whether the model endpoint has the same causal interpretation in humans.
- **Translation consequence:** what the conservation evidence permits the assay to decide and which species-specific reagent, humanized model, ex vivo human tissue, or human-cell bridge is required.

Do not substitute global percent identity for functional conservation. A conserved receptor with a divergent binding epitope can invalidate an antibody study; conserved proximal signaling with different tissue expression or circuit architecture can invalidate a phenotype claim. Conversely, a coarse wild-type endpoint may remain useful for exposure-response or safety even when disease translation is limited, provided the decision boundary is explicit.

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
