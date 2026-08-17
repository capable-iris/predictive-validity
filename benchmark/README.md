# Benchmark framework

Evaluates any target-scoring model against historical `(target × indication)` FDA approval outcomes.

## Task definition

**Given:** a (target × indication) pair and 40+ dimensions of preclinical evidence.
**Predict:** P(any drug on this T-I gets FDA-approved for THIS indication) — strict per-indication outcome.
**Ground truth:** `preclin.v_target_indication_strict_outcome.strict_approved_this_ti`.
**Cohort:** T-I pairs where ≥1 program reached Phase 1+ (n=13,821) or Phase 2+ (n=8,130).

## Scorer registry

Everything in `benchmark/scorers_*.py` implements the same interface:

```python
def scorer(evidence: dict, context: dict) -> dict:
    return {
        'predicted_p_approval': float in [0, 1],
        'predicted_tier': 'low' | 'medium' | 'high',
        'top_supporting_dims': [str],
        'top_concerning_dims': [str],
        'score_confidence': 'low' | 'medium' | 'high',
        'n_features_used': int,
    }
```

Registered scorers:

| File | Scorer names | Method |
|---|---|---|
| `scorers_rule_based.py` | random_v1, family_precedent_v1, nelson_only_v1, genetic_only_v1, rs_composite_v1 | Hand-weighted rule-based |
| `scorers_ml.py` | logreg_strict_v1, lightgbm_robust_strict_v1, randomforest_strict_v1 | Trained ML, 5-fold CV |
| `scorers_ensemble.py` | stacked_v2_hpo | LogReg meta-learner over base models |
| `scorers_pheiron.py` | pheiron_rs_composite_v2_hpo | Untrained published Pheiron RS |
| `scorers_llm_agent.py` | sonnet_agent_sdk_v1 | Claude Sonnet 4.6 reads evidence, predicts |

## Metrics

Every benchmark run stores:

- **AUC-ROC** — overall discrimination (bootstrap 95% CI, 200 draws)
- **Brier score** — calibration + refinement combined
- **Recall @ top-k%** — of top-k% scored T-Is, fraction of positives captured
- **Precision @ top-k%** — of top-k% scored T-Is, fraction that were positive
- **RS (top decile)** — P(approved | top 10%) / P(approved | rest). Direct Pheiron-comparable.
- **ECE (Expected Calibration Error)** — mean absolute gap between predicted probability and observed rate

## Run all baselines

```bash
export DATABASE_URL='...'
python3 runner.py                   # runs all registered rule-based scorers
python3 scorers_ml.py               # LogReg, LGB robust, RF on strict outcome
python3 scorers_ensemble.py         # stacked
python3 scorers_pheiron.py          # untrained Pheiron RS composite
python3 scorers_llm_agent.py 200    # Sonnet agent on 200 T-Is
```

## Plug in your own model

**Path 1** (Python callable): implement the scorer interface, `register_scorer(name, fn)`, run `runner.py <name>`.

**Path 2** (external CSV): produce `(target_id, indication_id, predicted_p_approval)` rows, wire in via `wire_external_scores()` in `external_template.py`.

## Query the leaderboard

```sql
SELECT * FROM preclin.v_benchmark_leaderboard;
```

The CSV snapshot at `../data/leaderboard.csv` mirrors this at commit time.

## Best scorer

For predicting FDA approval on strict per-indication outcome, **held-out target 5-fold CV, Phase 1+ cohort n=13,821**:

1. `stacked_final_v2_hpo` — AUC 0.821 (best overall)
2. `logreg_final_v2_hpo` — AUC 0.818 (best interpretable)
3. `pheiron_rs_composite_v2_hpo` (untrained, Phase 2+) — AUC 0.611

The paid LLM-agent comparison was not rerun for the HPO correction.

Full comparison: [`../RESULTS.md`](../RESULTS.md).
