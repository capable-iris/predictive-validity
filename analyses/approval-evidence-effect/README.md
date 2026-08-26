# Approval–evidence effect

This analysis estimates how target-linked GWAS studies and ClinGen
classifications change around a target's first drug approval. It is an
approval-associated cohort-time event study, not a causal estimate.

Apply migration 25, then refresh the normalized ChEMBL treatment tables only
when a new ChEMBL release is intentionally adopted:

```bash
.venv/bin/dotenv run -- sh -c \
  'psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f db/25_chembl_target_approval_history.sql'
.venv/bin/dotenv run -- .venv/bin/python analyses/approval-evidence-effect/fetch_chembl_target_approval_history.py
```

Reproduce the recorded analysis and 5,000-resample bootstrap:

```bash
.venv/bin/dotenv run -- .venv/bin/python analyses/approval-evidence-effect/approval_research_event_study.py --bootstrap 5000
```

The ChEMBL release, mapping provenance, target mappings, and molecule-level
events live in `preclin.*`; the analysis reads
`preclin.v_chembl_target_first_approval`. Generated analysis outputs live in
`data/` beside the scripts. The interpretation and headline estimates are
recorded in `RESULTS.md`.
