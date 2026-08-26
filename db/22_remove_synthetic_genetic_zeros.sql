-- Remove blanket zero fills that were not observations from the source data.
--
-- Missing HPO and Open Targets somatic relationships are interpreted as zero
-- recorded qualifying evidence only when assembling model features. Keeping
-- synthetic evidence_score rows would falsely present that interpretation as
-- a source-observed measurement and can leave stale duplicates after refresh.

BEGIN;

DELETE FROM preclin.evidence_score
WHERE subject_type = 'target'
  AND category = 'A_genetics'
  AND source_version = 'fill_zero_2026-08'
  AND extracted_by = 'script:fill_gaps'
  AND dimension IN ('n_hpo_phenotypes', 'ot_somatic_score_max');

COMMIT;
