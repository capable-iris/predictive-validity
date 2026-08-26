---
name: compile-target-report
description: Compile a source-backed target assessment with a two-page analytical PDF plus a source appendix for a focal indication. Use for target-quality reports covering phenotype direction, repository evidence tiers, tractable modalities, drug and patent landscape, translational in vitro and in vivo assays with model availability, and a target-to-indication mechanism diagram. Use independent specialist subagents and critics; do not use Neon unless the user explicitly restores it to scope.
---

# Compile a Target Report

Produce a decision-dense target assessment whose claims can survive an independent evidence audit. Treat the focal indication as required input; ask only if no reasonable indication can be inferred.

## Load the contracts

Read [references/evidence-contract.md](references/evidence-contract.md) before research and [references/report-contract.md](references/report-contract.md) before synthesis. Use `assets/report-theme.json` only when changing report styling.

## Orchestrate research

Use the project custom agents in `.codex/agents/`. Spawn independent, read-only specialists in batches that fit the available concurrency:

1. `target_phenotype_researcher` - target-wide phenotype inventory, modulation direction, tissue, and evidence tier.
2. `target_modality_researcher` - top one to three modalities from a first-principles generation pass followed by an evidence-based reality check.
3. `target_pipeline_researcher` - direct and claimed target-specific drugs/candidates, patent-only inventions, and status.
4. `target_assay_researcher` - in vitro and in vivo assays plus strict Phase 2 translation precedents.
5. `target_mechanism_researcher` - compact causal mechanism and uncertainty-aware diagram specification.

Tell every specialist the target symbol, aliases, focal indication, cutoff date, and the strict definition of a direct targeter. A specialist may spawn one narrow verification subagent when it materially separates source discovery from adjudication. Require source URLs or stable identifiers for every returned claim. Do not let specialists edit the final JSON or PDF.

## Require adversarial review

After the first-pass evidence is assembled, run both critics on the raw specialist outputs:

- `target_evidence_critic` independently searches for contradictory, missing, retracted, or weaker-than-claimed evidence and checks category/tier assignments.
- `target_translation_critic` independently verifies candidate status, assay setup claims, and every claimed Phase 2 translation precedent.

Give critics the research question and raw outputs, not the specialists' rationales about what the critics should conclude. Resolve every critic item as `accept`, `revise`, `downgrade`, or `exclude`; keep unresolved disagreements visible as limitations. Do not use a source solely because a specialist supplied it.

## Apply evidence gates

- Prefer primary papers, human genetics resources, official trial registries, regulatory documents, and sponsor disclosures for current status.
- Use reviews to find evidence, not as the only support for load-bearing claims when primary sources are available.
- Distinguish direct binding/functional targeters from pathway modulators and label disputed directness.
- Search patent databases for every candidate landscape, even when no asset has entered a clinical trial. Search the target symbol, aliases, protein name, modality classes, known inventors, and sponsors across Google Patents plus WIPO PATENTSCOPE, Espacenet, or the relevant national office when needed. Cover composition-of-matter, sequence/biologic, delivery/formulation, and target-specific use claims.
- Include the most decision-relevant patent-only inventions within the candidate row cap. Deduplicate patent families and record a representative publication or grant number, named assignee, priority/publication timing, and family/legal status only when verified.
- Label such rows `Patent-only` and state `No active program verified` unless a separate current source establishes development. A patent supports its disclosure, claims, examples, filing metadata, and legal status; it does not by itself establish efficacy, clinical status, present ownership, freedom to operate, or a live sponsor program.
- Use `claimed direct` when target directness rests on patent claims or patent examples unless an independent experimental source verifies direct engagement. Include ceased, abandoned, expired, pending, and granted families when decision-relevant, with status stated transparently.
- Never infer therapeutic direction from a disease association alone.
- Reconcile the phenotype inventory against the candidate, human-provocation, and safety evidence before synthesis. Every material disease or phenotype that appears as a direct or claimed-direct program indication, a human provocation/PD outcome, a termination driver, or a major modality liability must appear in the phenotype table or be named explicitly in limitations when the row cap prevents inclusion. Do not omit a prominent phenotype merely because causality is pathway-level, ligand-mediated, disputed, or negative in a target-specific trial. Instead, state the attribution boundary, use `unclear` modulation and the weakest justified category/strength when necessary, and distinguish "pathway implicated" from "target causal." A negative target-specific trial weakens target attribution; it does not erase the phenotype's decision relevance.
- Make the phenotype table target-wide and indication-agnostic. Search across organ systems, behaviors, development, metabolism, reproduction, immunity, safety liabilities, and disease phenotypes rather than using the focal indication as an inclusion gate. Include every material, source-supported phenotype domain that could change target direction, modality, safety, biomarkers, or indication choice. The focal indication still governs the assessment, modality ranking, assays, and mechanism endpoint.
- Deduplicate phenotype evidence by biological claim, not by indication. Merge outcomes only when perturbation class, effect direction, evidence category, and tissue context are materially aligned; keep opposing directions, distinct human syndromes, and safety phenotypes separate. Use the row cap for a compact comprehensive domain map, not a list of only indication-concordant findings. If the target has verified human pharmacodynamic engagement in any indication, include it; use a Human PD `Gap` only when none is verified target-wide.
- Keep modality strategy prospective and candidate landscape retrospective. Generate modality hypotheses before reviewing the existing pipeline so prior programs do not define the option set. Derive intervention requirements from modulation direction, target topology and compartment, pocket or interface geometry, endogenous ligand/substrate pharmacophore, catalytic versus scaffolding function, tissue and BBB access, turnover, homolog selectivity, exposure duration, and therapeutic-window constraints.
- For each ranked modality, separate cited observations from the conclusion they support. Label premises `Observed - target-specific` or `Observed - analogous target/class` and label the derived conclusion `First-principles inference`. Absence of a target-specific program is an evidence boundary, not evidence that the modality is implausible. Conversely, a physically plausible modality is not validated merely because an inference can be made.
- Require each modality row to state the strongest evidence boundary, the most credible failure mode, and the cheapest experiment that could reject the concept. Rank on mechanistic fit, physical feasibility, delivery, controllability, safety/selectivity, biomarkerability, and development burden; do not rank by the number of historical programs.
- For each modality, review the claims and verified legal status of the most relevant patent families and estimate the practical design-around burden as `Low`, `Moderate`, `High`, or `Unknown`. State which claimed features overlap the proposed modality and how much material differentiation may be needed in chemotype or sequence, binding site or epitope, signaling profile, delivery, formulation, route, dosing, or indication. Cite representative patent documents. This is an R&D planning screen, not a freedom-to-operate opinion; never imply non-infringement, validity, enforceability, ownership, or legal clearance without qualified patent counsel and a jurisdiction-specific claim chart.
- Count an assay as clinically predictive only when the same drug passed that assay (or the explicitly named same cell/animal line) and subsequently met its Phase 2 efficacy objective in the same indication. Otherwise write `None verified`.
- Organize assays as an iteration cascade, not a showcase of the most sophisticated models. Lead the in vitro table with the simplest practical cell-based assay that can establish direct target engagement or a proximal target-function change. Only then add downstream functional assays and complex disease models such as differentiated iPSCs or organoids. Prefer short turnaround, quantitative, exposure-response-capable assays in wild-type or off-the-shelf cells when they answer the question; do not promote a complex model merely because it appears more disease-like.
- Distinguish engagement from downstream phenotype. The lead assay must measure occupancy, binding in cells, target abundance/localization, or a target-proximal biochemical or functional event that can show the intervention reached and modulated the target. If no credible cell-based engagement assay is feasible, make that gap explicit and give the closest proximal pharmacodynamic assay rather than presenting a distal phenotype as engagement.
- Always investigate whether a wild-type animal can provide a rapid, reversible exposure-response or pharmacodynamic iteration signal for the target. Include a wild-type assay when it is informative; otherwise record why it would be insensitive or uninterpretable before proposing knockout, transgenic, induced, surgical, or disease models. A wild-type assay need not reproduce the disease if it can answer engagement, direction, dose, duration, tolerability, or a target-linked physiological question.
- Accept low-burden, coarse in vivo endpoints when they are adequate for early iteration. For example, home-cage activity or actigraphy-derived rest/activity can screen for sleep-like changes, but label it as an indirect sleep estimate and do not claim sleep stage, architecture, or definitive sleep without EEG/EMG or an equivalently validated measurement. Match endpoint precision to the decision being made.
- For every in vivo assay, assess conservation between the model species and humans at two levels: receptor/target conservation and pathway/phenotype conservation. Receptor review must cover orthology, isoforms, key ligand- or drug-contact residues, endogenous-ligand pharmacology, and demonstrated cross-species activity of the proposed modality where available. Pathway review must cover coupling/signaling, relevant tissue and cell-type expression, circuit or organ physiology, and whether the measured phenotype has the same causal interpretation. State the translational consequence, including species-specific reagents, humanized models, or human-cell bridging assays needed. Do not use global sequence identity alone as evidence of functional conservation.
- Specify every assay as a falsifiable measurement: name the measured analyte or endpoint, the exact edge or branch of the native mechanism it tests, and the expected positive and negative readouts. Include target-null/blocked, vehicle, inactive, or matched-genotype controls needed to interpret the call.
- Verify whether each exact cell or animal model exists. Search official repositories such as JAX, MMRRC, IMSR, ATCC, Coriell, RIKEN BRC, ECACC, and WiCell; record the stock/catalog ID and repository URL when orderable. Do not call a model easy merely because its ingredients exist.
- Score model availability internally from 0-3, but render plain-language labels: `Stocked` exact line is orderable with a stock/catalog ID; `Recoverable` is repository-registered but requires cryorecovery, sperm rederivation, or special access; `Published only` is documented in primary literature without verified public distribution; `Build` means the exact model must be assembled or engineered. Report orderable components separately; they do not upgrade a composite model.
- Score operational difficulty internally from 0-3, but render `Routine` for off-the-shelf execution, `Qualify` for a published protocol needing local qualification, `Specialist` for breeding/differentiation/aging/surgery/induction, and `Develop` for de novo engineering and validation.
- Record negative and null results, termination reasons, tissue mismatches, safety liabilities, and contradictory directionality.
- Include target-wide Human PD engagement in the phenotype evidence table, including an explicit `Gap` row only when no human engagement exists for the target in any indication.
- Sort phenotype evidence by decision strength: Genetic, Human PD, Animal, Cell, then Mechanistic.
- Keep perturbation class and phenotype direction independent. Put a neutral-color effect symbol beside the phenotype name: up/down arrows encode phenotype increase/decrease, a vertical bidirectional arrow encodes mixed effects, and a dash encodes unclear or no change. Do not tint or color the phenotype/effect cell by modulation. Encode modulation only in the separate modulation/result cell: teal for activation/agonism/GoF, red for inhibition/antagonism/LoF, and amber for mixed/unclear.
- Use `mixed` only when evidence supports opposing effects on the same named phenotype. If GoF and LoF cause different phenotypes but neither direction is established for the report phenotype, use `unclear` modulation and `unclear` effect direction.
- Make claims concrete: name the exact disease or phenotype, variant/class, assay/model, sample size, quantitative result, and contradictory comparator when available. Do not hide distinct findings inside umbrella phrases such as "pediatric syndromes," "positive models," or "clinical evidence."
- Outside the candidate landscape, follow every named drug, development candidate, or pharmacologic tool with a concise parenthetical that states both what it does and what it is, for example `ACD856 (oral pan-Trk small-molecule PAM)` or `BI 754132 (intravitreal TrkB-agonist mAb)`. Repeat the clarification in each cell or bullet where the name appears; do not assume the reader has seen another section. Formal source titles in the appendix are exempt.
- Do not use umbrella mechanism nodes such as "context limits" or "other risks." Give each mechanistically distinct modifier or liability its own node and direct citation. If the diagram cannot fit every branch legibly, omit the weakest claim instead of bundling unrelated claims under one citation.
- Draw the mechanism only under untreated, natural physiology: begin with the endogenous ligand or physiological input, pass through the native target and intracellular signaling, and end at the tissue function and indication-relevant phenotype. Do not place administered drugs, agonists, antagonists, antibodies, engineered activation, knockouts, trial outcomes, or treatment-response dependencies in mechanism nodes or edges. Perturbation studies may support citations, but the diagram itself must depict the native causal pathway. Put pharmacologic strategy and intervention-specific uncertainties in the evidence, modality, or candidate tables instead.
- Use named program outcomes as target-specific or analogous precedent where relevant, but do not require a program example for every modality claim. Source every observed premise and make unsupported engineering assumptions visible as boundaries or risks.
- Use the repository's category-specific 0-3 rubrics internally for validation and sorting, but never print numeric tiers in the PDF. Render `Strong`, `Moderate`, `Limited`, or `Gap` beside the evidence category. Put compact label sequences in the relevant section bars and the definitions in a rating key on the source appendix. These labels summarize strength within an evidence category and are not numerically interchangeable across categories. Nelson T0-T4 may be reported only as descriptive/audit context and never as a predictive feature.
- Never run paid Anthropic classifiers or LLM scorers without explicit approval.

## Synthesize and render

Populate one JSON document following [references/report-contract.md](references/report-contract.md). Keep prose telegraphic, cite source IDs in each claim, and stay within row and character limits.

Run:

```bash
.venv/bin/python .agents/skills/compile-target-report/scripts/validate_report.py INPUT.json
.venv/bin/python .agents/skills/compile-target-report/scripts/render_report.py INPUT.json OUTPUT.pdf
```

If the worktree has no `.venv`, use the repository's existing virtual environment by absolute path. Do not evaluate `.env` as shell code and do not use the database for this workflow.

The renderer requires `reportlab` and `pypdf`; visual QA requires Poppler's `pdftoppm`. Check imports in the active repository virtual environment. If Python packages are missing, install only those packages with `uv pip install --python .venv/bin/python reportlab pypdf`.

Render the PDF to PNGs and inspect all pages. Keep analysis to pages 1-2; place the rating key and sources on page 3, which does not count against the analytical limit. Reject clipped cells, unreadable citations, overlaps, broken arrows, placeholder text, more than two analytical pages, or more than one source appendix page. Run `validate_report.py --pdf OUTPUT.pdf` after rendering. Deliver the PDF and the source JSON so a reviewer can trace or revise it.
