"""Reclassify and recompute HPO phenotypic-abnormality breadth.

This migration uses the pinned official HPO ontology rather than term-name
heuristics. It counts only terms in the Phenotypic abnormality branch and
excludes annotations explicitly reported with zero frequency.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
from pathlib import Path

import psycopg2

from hpo_ontology import (
    EXPECTED_TERM_COUNT,
    ONTOLOGY_RELEASE,
    ensure_reference_table,
)


HERE = Path(__file__).resolve().parent

PHENOTYPE_COUNTS_SQL = """
SELECT gp.target_id,
       count(DISTINCT gp.hpo_id) FILTER (
         WHERE branch.hpo_id IS NOT NULL
           AND lower(coalesce(trim(gp.frequency), ''))
               NOT IN ('hp:0040285', '0', '0%', 'excluded')
           AND coalesce(trim(gp.frequency), '') !~ '^0\\s*/'
       )::double precision AS n_hpo_phenotypes
FROM public.gene_phenotypes gp
LEFT JOIN preclin.hpo_phenotypic_abnormality_term branch USING (hpo_id)
GROUP BY gp.target_id
"""


def checked_in_view_sql(path: Path, view_name: str) -> str:
    """Extract one checked-in CREATE VIEW body without reading deployed SQL."""
    source = path.read_text()
    pattern = re.compile(
        rf"CREATE VIEW {re.escape(view_name)} AS\n(.*?);\n\nCOMMENT ON VIEW {re.escape(view_name)}",
        re.DOTALL,
    )
    match = pattern.search(source)
    if not match:
        raise ValueError(f"could not find checked-in definition for {view_name} in {path}")
    return f"CREATE OR REPLACE VIEW {view_name} AS\n{match.group(1)}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--ontology",
        type=Path,
        help="Optional local copy of the pinned hp.obo release; digest is verified",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def fingerprint(cur) -> str:
    cur.execute(
        """
        SELECT subject_id, value_numeric
        FROM preclin.evidence_score
        WHERE subject_type = 'target'
          AND dimension = 'n_hpo_phenotypes'
          AND source = 'genome_browser_derived'
        ORDER BY subject_id
        """
    )
    payload = "\n".join(f"{subject_id}:{value:g}" for subject_id, value in cur.fetchall())
    return hashlib.md5(payload.encode(), usedforsecurity=False).hexdigest()


def evidence_stats(cur) -> tuple[int, float, float, float, str]:
    cur.execute(
        """
        SELECT count(*), min(value_numeric), max(value_numeric), avg(value_numeric)
        FROM preclin.evidence_score
        WHERE subject_type = 'target'
          AND dimension = 'n_hpo_phenotypes'
          AND source = 'genome_browser_derived'
        """
    )
    rows, minimum, maximum, average = cur.fetchone()
    return rows, minimum, maximum, average, fingerprint(cur)


def main() -> None:
    args = parse_args()
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        cur = conn.cursor()
        before = evidence_stats(cur)
        term_count = ensure_reference_table(cur, args.ontology)
        if term_count != EXPECTED_TERM_COUNT:
            raise RuntimeError("HPO reference table is incomplete")

        cur.execute(
            """
            UPDATE preclin.evidence_dimension
            SET category = 'A_genetics',
                description = %s
            WHERE dimension = 'n_hpo_phenotypes'
            """,
            (
                "Distinct positively observed HPO Phenotypic abnormality terms "
                f"(HP:0000118 branch, HPO {ONTOLOGY_RELEASE}); target-level and indication-agnostic",
            ),
        )
        if cur.rowcount != 1:
            raise RuntimeError("n_hpo_phenotypes is missing from evidence_dimension")

        cur.execute(
            """
            UPDATE preclin.evidence_score
            SET category = 'A_genetics'
            WHERE dimension = 'n_hpo_phenotypes'
              AND category IS DISTINCT FROM 'A_genetics'
            """
        )
        category_updates = cur.rowcount

        cur.execute(
            f"""
            WITH phenotype_counts AS ({PHENOTYPE_COUNTS_SQL})
            UPDATE preclin.evidence_score es
            SET value_numeric = pc.n_hpo_phenotypes,
                extracted_at = now()
            FROM phenotype_counts pc
            WHERE es.subject_type = 'target'
              AND es.subject_id = pc.target_id
              AND es.dimension = 'n_hpo_phenotypes'
              AND es.source = 'genome_browser_derived'
              AND es.value_numeric IS DISTINCT FROM pc.n_hpo_phenotypes
            """
        )
        value_updates = cur.rowcount

        cur.execute(checked_in_view_sql(HERE / "05_ti_views.sql", "preclin.v_relative_success"))
        cur.execute(
            checked_in_view_sql(
                HERE / "07_analysis_views.sql", "preclin.v_relative_success_clean"
            )
        )

        cur.execute(
            f"""
            WITH phenotype_counts AS ({PHENOTYPE_COUNTS_SQL})
            SELECT count(*)
            FROM preclin.evidence_score es
            JOIN phenotype_counts pc ON pc.target_id = es.subject_id
            WHERE es.subject_type = 'target'
              AND es.dimension = 'n_hpo_phenotypes'
              AND es.source = 'genome_browser_derived'
              AND (
                es.category IS DISTINCT FROM 'A_genetics'
                OR es.value_numeric IS DISTINCT FROM pc.n_hpo_phenotypes
              )
            """
        )
        if cur.fetchone()[0] != 0:
            raise RuntimeError("HPO evidence migration did not reach its postcondition")

        after = evidence_stats(cur)

        if args.dry_run:
            conn.rollback()
            disposition = "validated; rolled back"
        else:
            conn.commit()
            disposition = "committed"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(
        f"HPO release={ONTOLOGY_RELEASE} terms={term_count} "
        f"category_updates={category_updates} value_updates={value_updates}"
    )
    print(
        f"before rows={before[0]} range={before[1]:g}-{before[2]:g} "
        f"average={before[3]:.6f} fingerprint={before[4]}"
    )
    print(
        f"after rows={after[0]} range={after[1]:g}-{after[2]:g} "
        f"average={after[3]:.6f} fingerprint={after[4]}"
    )
    print(disposition)


if __name__ == "__main__":
    main()
