# Report data contract

The renderer accepts one UTF-8 JSON object. Keep entries concise so the PDF remains legible.

## Required top-level fields

- `target`: `symbol`, `name`, `aliases` (array), `indication`, `as_of` (`YYYY-MM-DD`).
- `assessment`: `verdict`, `confidence` (`Low`, `Moderate`, or `High`), `opportunity`, `key_risk`, `limitations` (array, maximum 3).
- `phenotypes`: 1-7 rows with `phenotype`, `modulation`, `effect_direction`, `effect`, `category`, `score` (0-3), `evidence`, `tissue`, `sources`.
- `modalities`: 1-3 rows with `modality`, `rank` (1-3), `pros`, `cons`, `sources`. Every pro and con item must contain a named program/example and its result; there is no separate examples field.
- `candidates`: 0-8 rows with `name`, `modality`, `sponsor`, `route`, `directness`, `indication`, `status`, `reason`, `sources`. Rows may represent named development candidates or decision-relevant patent-family inventions.
- `in_vitro_assays` and `in_vivo_assays`: 1-3 rows each with `method`, `assay`, `measured`, `mechanism_link`, `positive_readout`, `negative_readout`, `model`, `model_availability_score` (0-3), `model_availability`, `setup_difficulty_score` (0-3), `setup`, `phase2_precedent`, `sources`.
- `mechanism`: `caption`, 3-8 `nodes`, and 2-10 `edges`. Nodes require `id`, `label`, `x`, `y`, and `sources`, where coordinates are 0-1. Edges require `from`, `to`, `label`, and `sources`.
- `sources`: 1-40 rows with unique `id` such as `S1`, `citation`, `url`, `type`, and optional `doi`, `pmid`, or `nct`. The rating key and sources render on a separate third page and do not count against the two-page analytical limit.

Every `sources` array elsewhere contains source IDs declared in the top-level source list. Every evidence, status, pros/cons example, assay precedent, and mechanism claim must cite at least one source.

For a patent-only candidate, include a representative publication or grant number in `name` or `status`, the document's named assignee in `sponsor`, a specifically claimed route or `Not disclosed`, and `Patent-only; no active program verified` in `status` or `reason`. Patent claims alone support `claimed direct`, not `direct`. Do not imply clinical development, efficacy, current ownership, or freedom to operate from a filing.

The renderer treats modulation and effect direction independently. The effect symbol appears in the phenotype column: up arrow for `increase`, down arrow for `decrease`, vertical bidirectional arrow for `mixed`, and a dash for `unclear` or `no change`. The phenotype/effect cell and symbol use a neutral color and must not encode modulation. Only the separate modulation/result cell is color-coded: teal for `agonism`, `activation`, or `gain of function`; red for `antagonism`, `inhibition`, or `loss of function`; amber for `mixed` or `unclear`. Modality `pros` and `cons` render as vertical bullet lists, one named program/result per bullet.

Use `mixed` only for opposing effects on the same named phenotype. Use `unclear` when perturbation direction is unresolved for that phenotype, even if separate GoF and LoF variants cause different disorders.

Prefer exact, decision-relevant facts over umbrella prose. Name specific phenotypes, variants/classes, sample sizes, models, endpoints, magnitudes, timing, and null comparators when verified and space permits.

Every named drug, development candidate, or pharmacologic tool outside the candidate landscape must be followed immediately by a parenthetical that states mechanism/action and modality, such as `ACD856 (oral pan-Trk small-molecule PAM)`. Repeat the parenthetical in every separate cell or bullet; readers must not need to cross-reference the candidate table. Endogenous ligands, generic modality classes, assay reagents that are not pharmacologic tools, and formal source titles are exempt.

Mechanism nodes must be claim-specific. Do not combine distinct isoform effects, alternate receptors, signaling liabilities, tumor biology, or other caveats into an umbrella node such as `context limits`. Each distinct branch needs its own node and a source that directly supports that branch; omit a weak branch if it cannot be shown legibly and cited accurately.

The mechanism diagram represents untreated, natural physiology only. Start with an endogenous ligand or physiological input, connect it to the native target and intracellular signaling, and end with tissue functions and the indication-relevant phenotype. Do not use administered compounds, agonists, antagonists, antibodies, engineered activation, knockouts, clinical outcomes, or treatment-response dependencies as nodes or edge labels. Experimental perturbations may establish an edge and appear in its citation, but not in the displayed pathway. Keep intervention strategy and uncertainty in the surrounding tables.

Every assay must state exactly what is measured, the native-mechanism edge or branch tested, and explicit positive and negative calls. Keep the following numeric values only in JSON for validation and sorting; do not print them in the PDF. `model_availability_score` maps to: `3` `Stocked`, exact line publicly/commercially obtainable; `2` `Recoverable`, repository-registered but cryorecovery, sperm rederivation, or special access required; `1` `Published only`, documented in primary literature without verified public distribution; `0` `Build`, exact line must be assembled or engineered. Report component stocks without upgrading the composite. `setup_difficulty_score` maps to: `0` `Routine`, off-the-shelf; `1` `Qualify`, published protocol needing local qualification; `2` `Specialist`, breeding/differentiation/aging/surgery/induction; `3` `Develop`, de novo engineering and validation.

Keep phenotype `score` values only in JSON. Render `3` as `Strong`, `2` as `Moderate`, `1` as `Limited`, and `0` as `Gap`, always beside the evidence category. The label expresses strength within that category, not equivalence between Genetic, Human PD, Animal, Cell, and Mechanistic evidence. Put the compact sequences in section headers and the definitions in the source-appendix rating key.

## Length limits

The validator enforces row and text limits. Prefer compact language rather than shrinking the font:

- title-like fields: 80 characters;
- evidence/status/setup cells: 190 characters;
- assay measured/mechanism/positive/negative/model-availability cells: 180 characters each;
- pros and cons: at most 3 items each, 150 characters per item;
- source citations: 180 characters;
- assessment verdict: 220 characters;
- mechanism node labels: 70 characters; edge labels: 45 characters.

Use `None verified` for missing strict translation precedents and `Not applicable` only when the field genuinely cannot apply.

## Example skeleton

```json
{
  "target": {"symbol": "GENE", "name": "Protein", "aliases": [], "indication": "Disease", "as_of": "2026-08-20"},
  "assessment": {"verdict": "...", "confidence": "Moderate", "opportunity": "...", "key_risk": "...", "limitations": ["..."]},
  "phenotypes": [],
  "modalities": [],
  "candidates": [],
  "in_vitro_assays": [],
  "in_vivo_assays": [],
  "mechanism": {"caption": "...", "nodes": [], "edges": []},
  "sources": []
}
```
