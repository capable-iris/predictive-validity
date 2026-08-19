"""Extend preclin.evidence_score with the untapped genome-browser tables.

Adds evidence dimensions we missed:
- tau_specificity, max_tissue_tpm, max_tissue (from tissue_expression_summary)
- n_causal_diseases, n_suggestive_diseases (target_pleiotropy)
- n_reactome_pathways (from target_pathways)
- n_ppi_partners (protein_interactions)
- n_dgidb_drugs (drug_gene_interactions)
- n_hpo_phenotypes (gene_phenotypes)
- ot_l2g_score_max (from target_evidence.l2g_score)
- ot_somatic_score_max (from target_evidence.somatic_score)
- ot_rna_expression_max (from target_evidence.rna_expression_score)
- ot_is_mendelian_any (from target_evidence.is_mendelian aggregate)

Runs against Neon directly — pulls from public.*, inserts into preclin.evidence_score.
Idempotent via ON CONFLICT.
"""

import os
import time
import psycopg2
from psycopg2.extras import execute_values


def with_retry(conn, fn, max_attempts=5):
    """Retry with reconnect on network / transient errors."""
    for attempt in range(max_attempts):
        try:
            return fn(conn)
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            if attempt == max_attempts - 1:
                raise
            wait = 2 ** attempt
            print(f"  [retry {attempt+1}/{max_attempts}] {type(e).__name__}: reconnecting in {wait}s",
                  flush=True)
            time.sleep(wait)
            try:
                conn.close()
            except Exception:
                pass
            conn = psycopg2.connect(os.environ["DATABASE_URL"])
    return conn

DB_URL = os.environ["DATABASE_URL"]

# Registry additions
NEW_DIMENSIONS = [
    # Category B (mechanistic — tissue specificity is relevant for druggability)
    ("tau_specificity", "B_mechanistic", "target", "numeric_float",
     "Yanai Tau specificity score (0=broad, 1=highly tissue-specific)",
     "tissue_expression_summary", None),
    ("max_tissue_tpm", "B_mechanistic", "target", "numeric_float",
     "Max expression value across GTEx/HPA tissues", "tissue_expression_summary", None),
    ("max_tissue_name", "B_mechanistic", "target", "text",
     "Tissue with highest expression", "tissue_expression_summary", None),
    ("n_high_tissues", "B_mechanistic", "target", "count",
     "Number of tissues where target is highly expressed", "tissue_expression_summary", None),
    # Category B — druggability network
    ("n_ppi_partners", "B_mechanistic", "target", "count",
     "STRING high-confidence protein interaction partners", "protein_interactions", None),
    ("n_reactome_pathways", "B_mechanistic", "target", "count",
     "Number of Reactome pathways this target belongs to", "target_pathways", None),
    # Category I (landscape) — drug precedent
    ("n_dgidb_drugs", "I_landscape", "target", "count",
     "DGIdb: number of drugs known to interact with this target", "drug_gene_interactions", None),
    # Category I — pleiotropy signal
    ("n_causal_diseases", "I_landscape", "target", "count",
     "Number of diseases with causal evidence for this target",
     "target_pleiotropy", None),
    ("n_suggestive_diseases", "I_landscape", "target", "count",
     "Number of diseases with suggestive evidence",
     "target_pleiotropy", None),
    # Category A — target-level human genetic phenotype breadth
    ("n_hpo_phenotypes", "A_genetics", "target", "count",
     "Distinct Human Phenotype Ontology terms linked to human gene-disease annotations; target-level, indication-agnostic, and excludes mode-of-inheritance terms",
     "gene_phenotypes", None),
    # Category A — Open Targets granular
    ("ot_l2g_score_max", "A_genetics", "target", "numeric_float",
     "Max locus-to-gene score from Open Targets (colocalization signal)",
     "target_evidence", None),
    ("ot_somatic_score_max", "A_genetics", "target", "numeric_float",
     "Max Open Targets somatic mutation (cancer) score", "target_evidence", None),
    ("ot_rna_expression_max", "A_genetics", "target", "numeric_float",
     "Max Open Targets RNA expression score", "target_evidence", None),
    ("ot_is_mendelian_any", "A_genetics", "target", "boolean",
     "Any Open Targets evidence marked mendelian", "target_evidence", None),
    # Category A — expanded genetics granularity
    ("mendelian_n_dominant", "A_genetics", "target", "count",
     "Mendelian associations with dominant inheritance", "mendelian_associations", None),
    ("mendelian_n_recessive", "A_genetics", "target", "count",
     "Mendelian associations with recessive inheritance", "mendelian_associations", None),
]

# Ingest queries — each yields evidence_score rows
INGEST_QUERIES = [
    ("tau_specificity", "B_mechanistic", "target", "value_numeric",
     "SELECT target_id, tau_score FROM public.tissue_expression_summary WHERE tau_score IS NOT NULL"),
    ("max_tissue_tpm", "B_mechanistic", "target", "value_numeric",
     "SELECT target_id, max_value FROM public.tissue_expression_summary WHERE max_value IS NOT NULL"),
    ("max_tissue_name", "B_mechanistic", "target", "value_text",
     "SELECT target_id, max_tissue FROM public.tissue_expression_summary WHERE max_tissue IS NOT NULL"),
    ("n_high_tissues", "B_mechanistic", "target", "value_numeric",
     "SELECT target_id, n_high_tissues FROM public.tissue_expression_summary WHERE n_high_tissues IS NOT NULL"),
    ("n_ppi_partners", "B_mechanistic", "target", "value_numeric",
     "SELECT target_a_id, count(*) FROM public.protein_interactions WHERE combined_score >= 700 GROUP BY target_a_id"),
    ("n_reactome_pathways", "B_mechanistic", "target", "value_numeric",
     "SELECT target_id, count(*) FROM public.target_pathways GROUP BY target_id"),
    ("n_dgidb_drugs", "I_landscape", "target", "value_numeric",
     "SELECT target_id, count(DISTINCT drug_chembl_id) FROM public.drug_gene_interactions WHERE target_id IS NOT NULL GROUP BY target_id"),
    ("n_causal_diseases", "I_landscape", "target", "value_numeric",
     "SELECT target_id, n_causal_diseases FROM public.target_pleiotropy WHERE n_causal_diseases IS NOT NULL"),
    ("n_suggestive_diseases", "I_landscape", "target", "value_numeric",
     "SELECT target_id, n_suggestive_diseases FROM public.target_pleiotropy WHERE n_suggestive_diseases IS NOT NULL"),
    ("n_hpo_phenotypes", "A_genetics", "target", "value_numeric",
     """SELECT gp.target_id,
               count(DISTINCT gp.hpo_id) FILTER (
                 WHERE p.name IS NULL OR p.name NOT ILIKE '%inheritance%'
               )
          FROM public.gene_phenotypes gp
          LEFT JOIN public.phenotypes p USING (hpo_id)
         GROUP BY gp.target_id"""),
    ("ot_l2g_score_max", "A_genetics", "target", "value_numeric",
     "SELECT target_id, MAX(l2g_score) FROM public.target_evidence WHERE l2g_score IS NOT NULL GROUP BY target_id"),
    ("ot_somatic_score_max", "A_genetics", "target", "value_numeric",
     "SELECT target_id, MAX(somatic_score) FROM public.target_evidence WHERE somatic_score IS NOT NULL GROUP BY target_id"),
    ("ot_rna_expression_max", "A_genetics", "target", "value_numeric",
     "SELECT target_id, MAX(rna_expression_score) FROM public.target_evidence WHERE rna_expression_score IS NOT NULL GROUP BY target_id"),
    ("mendelian_n_dominant", "A_genetics", "target", "value_numeric",
     """SELECT target_id, count(*) FROM public.mendelian_associations
        WHERE inheritance ILIKE '%dominant%' GROUP BY target_id"""),
    ("mendelian_n_recessive", "A_genetics", "target", "value_numeric",
     """SELECT target_id, count(*) FROM public.mendelian_associations
        WHERE inheritance ILIKE '%recessive%' GROUP BY target_id"""),
]


def main():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    # Register dimensions
    print("Registering dimensions...")
    for dim, cat, subj, dtype, desc, source, tier in NEW_DIMENSIONS:
        cur.execute("""
            INSERT INTO preclin.evidence_dimension
              (dimension, category, subject_type, data_type, description, source_primary)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (dimension) DO UPDATE SET
              category = EXCLUDED.category,
              description = EXCLUDED.description,
              source_primary = EXCLUDED.source_primary
        """, (dim, cat, subj, dtype, desc, source))
    conn.commit()

    # Ingest each dimension
    for dim, cat, subj_type, val_col, query in INGEST_QUERIES:
        source = dim.split("_")[0] if "_" in dim else "gb"
        source_name_map = {
            "tau": "hpa_gtex_summary", "max": "hpa_gtex_summary",
            "n": "genome_browser_derived",
            "ot": "open_targets", "mendelian": "orphanet_omim",
        }
        prefix = dim.split("_")[0]
        source_str = source_name_map.get(prefix, "genome_browser_derived")

        print(f"Ingesting {dim} ...", end=" ", flush=True)
        cur.execute(query)
        rows = cur.fetchall()

        insert_rows = []
        for r in rows:
            tid, val = r[0], r[1]
            if val is None:
                continue
            if val_col == "value_numeric":
                insert_rows.append(("target", tid, None, dim, cat,
                                    float(val), None, None,
                                    source_str, "2026", None, None,
                                    "script:05_ingest_extra"))
            elif val_col == "value_text":
                insert_rows.append(("target", tid, None, dim, cat,
                                    None, str(val)[:200], None,
                                    source_str, "2026", None, None,
                                    "script:05_ingest_extra"))
            elif val_col == "value_boolean":
                insert_rows.append(("target", tid, None, dim, cat,
                                    None, None, bool(val),
                                    source_str, "2026", None, None,
                                    "script:05_ingest_extra"))

        if insert_rows:
            execute_values(cur, """
                INSERT INTO preclin.evidence_score
                  (subject_type, subject_id, subject_id2, dimension, category,
                   value_numeric, value_text, value_boolean, source, source_version,
                   confidence, citation_pmids, extracted_by)
                VALUES %s
                ON CONFLICT (subject_type, subject_id, subject_id2, dimension, source, source_version)
                DO UPDATE SET
                  category = EXCLUDED.category,
                  value_numeric = EXCLUDED.value_numeric,
                  value_text = EXCLUDED.value_text,
                  value_boolean = EXCLUDED.value_boolean,
                  extracted_at = now()
            """, insert_rows, page_size=2000)
        conn.commit()
        print(f"{len(insert_rows)} rows")

        # Log
        cur.execute("""
            INSERT INTO preclin.ingest_log
              (source_file, target_table, rows_read, rows_inserted, finished_at, status, notes)
            VALUES (%s, %s, %s, %s, now(), 'ok', %s)
        """, (f"public.{source_str}", "preclin.evidence_score",
              len(rows), len(insert_rows), f"dimension={dim}"))
        conn.commit()

    # Special: ot_is_mendelian_any (boolean aggregate)
    print("Ingesting ot_is_mendelian_any ...", end=" ", flush=True)
    cur.execute("""
        SELECT target_id, bool_or(is_mendelian)
        FROM public.target_evidence
        WHERE is_mendelian IS NOT NULL
        GROUP BY target_id
    """)
    rows = cur.fetchall()
    insert_rows = []
    for tid, val in rows:
        if val is None:
            continue
        insert_rows.append(("target", tid, None, "ot_is_mendelian_any", "A_genetics",
                            None, None, bool(val),
                            "open_targets", "2026", None, None,
                            "script:05_ingest_extra"))
    if insert_rows:
        execute_values(cur, """
            INSERT INTO preclin.evidence_score
              (subject_type, subject_id, subject_id2, dimension, category,
               value_numeric, value_text, value_boolean, source, source_version,
               confidence, citation_pmids, extracted_by)
            VALUES %s
            ON CONFLICT (subject_type, subject_id, subject_id2, dimension, source, source_version)
            DO UPDATE SET
              category = EXCLUDED.category,
              value_boolean = EXCLUDED.value_boolean,
              extracted_at = now()
        """, insert_rows, page_size=2000)
    conn.commit()
    print(f"{len(insert_rows)} rows")

    print("\nDone. Extra evidence dimensions ingested to preclin.evidence_score.")


if __name__ == "__main__":
    main()
