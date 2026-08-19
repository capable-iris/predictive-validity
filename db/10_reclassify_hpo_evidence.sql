-- Reclassify n_hpo_phenotypes from animal evidence to human genetics.
--
-- public.gene_phenotypes contains human gene-to-HPO annotations associated
-- with OMIM and ORPHA disease identifiers. This migration changes taxonomy
-- metadata only: it deliberately leaves the stored feature values unchanged.

BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM preclin.evidence_dimension
    WHERE dimension = 'n_hpo_phenotypes'
  ) THEN
    RAISE EXCEPTION 'n_hpo_phenotypes is missing from preclin.evidence_dimension';
  END IF;
END
$$;

UPDATE preclin.evidence_dimension
SET category = 'A_genetics',
    description = 'Distinct Human Phenotype Ontology terms linked to human gene-disease annotations; target-level and indication-agnostic'
WHERE dimension = 'n_hpo_phenotypes';

UPDATE preclin.evidence_score
SET category = 'A_genetics'
WHERE dimension = 'n_hpo_phenotypes'
  AND category IS DISTINCT FROM 'A_genetics';

-- Preserve the deployed definitions of these potentially expensive views and
-- change only the HPO label/category literals. This avoids replacing
-- deployment-specific cohort logic with a repository snapshot.
DO $$
DECLARE
  view_name text;
  view_oid regclass;
  old_definition text;
  new_definition text;
BEGIN
  FOREACH view_name IN ARRAY ARRAY[
    'preclin.v_relative_success',
    'preclin.v_relative_success_clean'
  ]
  LOOP
    view_oid := to_regclass(view_name);
    IF view_oid IS NULL THEN
      CONTINUE;
    END IF;

    old_definition := pg_get_viewdef(view_oid, true);
    new_definition := replace(
      old_definition,
      '''D. HPO phenotypes ≥10''::text,
            ''D_animal''::text',
      '''A. HPO phenotype breadth ≥10''::text,
            ''A_genetics''::text'
    );
    new_definition := replace(
      new_definition,
      '''D. HPO ≥10 phenotypes''::text,
            ''D_animal''::text',
      '''A. HPO phenotype breadth ≥10''::text,
            ''A_genetics''::text'
    );

    IF new_definition = old_definition THEN
      IF position('''A. HPO phenotype breadth ≥10''::text' IN old_definition) > 0
         AND position('''A_genetics''::text' IN old_definition) > 0 THEN
        CONTINUE;
      END IF;
      RAISE EXCEPTION 'Could not locate the HPO category branch in %', view_name;
    END IF;

    EXECUTE format('CREATE OR REPLACE VIEW %s AS %s', view_oid, new_definition);
  END LOOP;
END
$$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM preclin.evidence_dimension
    WHERE dimension = 'n_hpo_phenotypes'
      AND category IS DISTINCT FROM 'A_genetics'
  ) OR EXISTS (
    SELECT 1
    FROM preclin.evidence_score
    WHERE dimension = 'n_hpo_phenotypes'
      AND category IS DISTINCT FROM 'A_genetics'
  ) THEN
    RAISE EXCEPTION 'n_hpo_phenotypes category migration did not reach its postcondition';
  END IF;
END
$$;

COMMIT;
