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

1. `target_phenotype_researcher` - phenotype, modulation direction, tissue, and evidence tier.
2. `target_modality_researcher` - top one to three modalities and real program-backed pros/cons.
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
- Count an assay as clinically predictive only when the same drug passed that assay (or the explicitly named same cell/animal line) and subsequently met its Phase 2 efficacy objective in the same indication. Otherwise write `None verified`.
- Specify every assay as a falsifiable measurement: name the measured analyte or endpoint, the exact edge or branch of the native mechanism it tests, and the expected positive and negative readouts. Include target-null/blocked, vehicle, inactive, or matched-genotype controls needed to interpret the call.
- Verify whether each exact cell or animal model exists. Search official repositories such as JAX, MMRRC, IMSR, ATCC, Coriell, RIKEN BRC, ECACC, and WiCell; record the stock/catalog ID and repository URL when orderable. Do not call a model easy merely because its ingredients exist.
- Score model availability internally from 0-3, but render plain-language labels: `Stocked` exact line is orderable with a stock/catalog ID; `Recoverable` is repository-registered but requires cryorecovery, sperm rederivation, or special access; `Published only` is documented in primary literature without verified public distribution; `Build` means the exact model must be assembled or engineered. Report orderable components separately; they do not upgrade a composite model.
- Score operational difficulty internally from 0-3, but render `Routine` for off-the-shelf execution, `Qualify` for a published protocol needing local qualification, `Specialist` for breeding/differentiation/aging/surgery/induction, and `Develop` for de novo engineering and validation.
- Record negative and null results, termination reasons, tissue mismatches, safety liabilities, and contradictory directionality.
- Include Human PD engagement in the phenotype evidence table, including an explicit `Gap` row when no human engagement exists.
- Sort phenotype evidence by decision strength: Genetic, Human PD, Animal, Cell, then Mechanistic.
- Keep perturbation class and phenotype direction independent. Put a neutral-color effect symbol beside the phenotype name: up/down arrows encode phenotype increase/decrease, a vertical bidirectional arrow encodes mixed effects, and a dash encodes unclear or no change. Do not tint or color the phenotype/effect cell by modulation. Encode modulation only in the separate modulation/result cell: teal for activation/agonism/GoF, red for inhibition/antagonism/LoF, and amber for mixed/unclear.
- Use `mixed` only when evidence supports opposing effects on the same named phenotype. If GoF and LoF cause different phenotypes but neither direction is established for the report phenotype, use `unclear` modulation and `unclear` effect direction.
- Make claims concrete: name the exact disease or phenotype, variant/class, assay/model, sample size, quantitative result, and contradictory comparator when available. Do not hide distinct findings inside umbrella phrases such as "pediatric syndromes," "positive models," or "clinical evidence."
- Outside the candidate landscape, follow every named drug, development candidate, or pharmacologic tool with a concise parenthetical that states both what it does and what it is, for example `ACD856 (oral pan-Trk small-molecule PAM)` or `BI 754132 (intravitreal TrkB-agonist mAb)`. Repeat the clarification in each cell or bullet where the name appears; do not assume the reader has seen another section. Formal source titles in the appendix are exempt.
- Do not use umbrella mechanism nodes such as "context limits" or "other risks." Give each mechanistically distinct modifier or liability its own node and direct citation. If the diagram cannot fit every branch legibly, omit the weakest claim instead of bundling unrelated claims under one citation.
- Draw the mechanism only under untreated, natural physiology: begin with the endogenous ligand or physiological input, pass through the native target and intracellular signaling, and end at the tissue function and indication-relevant phenotype. Do not place administered drugs, agonists, antagonists, antibodies, engineered activation, knockouts, trial outcomes, or treatment-response dependencies in mechanism nodes or edges. Perturbation studies may support citations, but the diagram itself must depict the native causal pathway. Put pharmacologic strategy and intervention-specific uncertainties in the evidence, modality, or candidate tables instead.
- Fold named program examples and outcomes into bulleted modality pro and con cells; do not create a separate examples column or join multiple points into sentence prose.
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
