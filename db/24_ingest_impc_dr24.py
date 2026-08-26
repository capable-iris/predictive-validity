"""Ingest audited IMPC DR24 updates for Phase 1+ targets missing in 2025.

Only one-to-one human-to-mouse mappings receive numeric phenotype counts.
Zero is eligible only when the current IMPC release reports phenotyping data,
no significant MP terms, and at least 13 successful homozygous procedures.
All other states remain NULL for the numeric feature and are retained in an
explicit status dimension. The script also removes the historical blanket-zero
backfill and writes a target-level before/after ledger.
"""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data" / "impc_missing_update_dr24.csv"
LEDGER = ROOT / "data" / "impc_dr24_feature_changes.csv"
EXPECTED_SHA256 = "06ff3876a9d8f45da7a7b60c20be351c102a402e118a32105698d5fb5a1042f7"
SOURCE_VERSION = "DR24"
EXTRACTED_BY = "script:ingest_impc_dr24"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def load_rows():
    digest = hashlib.sha256(INPUT.read_bytes()).hexdigest()
    if digest != EXPECTED_SHA256:
        raise RuntimeError(
            f"Unexpected DR24 audit digest {digest}; expected {EXPECTED_SHA256}"
        )
    with INPUT.open() as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 444 or len({row["target_id"] for row in rows}) != 444:
        raise RuntimeError("Expected exactly 444 unique audited targets")
    return rows, digest


def bool_value(value):
    return value.lower() == "true"


def resolved_status(row):
    if row["status"] == "no_mouse_mapping":
        return "mapping_unresolved"
    if bool_value(row["mapping_ambiguous"]):
        return "mapping_ambiguous"
    if row["status"] == "current_significant_phenotype":
        return "significant_phenotype"
    if bool_value(row["eligible_observed_zero"]):
        return "adequately_phenotyped_no_significant"
    if row["status"] == "phenotyped_no_significant_phenotype":
        return "insufficient_phenotyping"
    if row["status"] == "mapped_no_phenotyping":
        return "not_phenotyped"
    return "mapping_unresolved"


def proposed_value(row):
    if bool_value(row["mapping_ambiguous"]):
        return None
    if row["status"] == "current_significant_phenotype":
        return float(row["distinct_significant_mp_terms_sum"])
    if bool_value(row["eligible_observed_zero"]):
        return 0.0
    return None


def write_ledger(rows, old_values):
    fields = [
        "target_id", "human_symbol", "old_feature_value", "new_feature_value",
        "resolved_status", "change", "mouse_symbols",
        "homozygous_tested_procedures", "impc_release", "release_date",
    ]
    if LEDGER.exists():
        with LEDGER.open() as handle:
            existing = list(csv.DictReader(handle))
        if len(existing) != 444 or len({r["target_id"] for r in existing}) != 444:
            raise RuntimeError(f"Existing IMPC change ledger is invalid: {LEDGER}")
        return
    with LEDGER.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            target_id = int(row["target_id"])
            old = old_values.get(target_id)
            new = proposed_value(row)
            if old is None and new is None:
                change = "remains_unknown"
            elif old == new:
                change = "unchanged"
            elif old is None:
                change = "unknown_to_observed"
            elif new is None:
                change = "synthetic_zero_to_unknown"
            else:
                change = "synthetic_zero_to_observed"
            writer.writerow({
                "target_id": target_id,
                "human_symbol": row["human_symbol"],
                "old_feature_value": "" if old is None else old,
                "new_feature_value": "" if new is None else new,
                "resolved_status": resolved_status(row),
                "change": change,
                "mouse_symbols": row["mouse_symbols"],
                "homozygous_tested_procedures": row[
                    "homozygous_tested_procedures"
                ],
                "impc_release": row["impc_release"],
                "release_date": row["release_date"],
            })


def main():
    args = parse_args()
    rows, digest = load_rows()
    target_ids = [int(row["target_id"]) for row in rows]

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, symbol FROM public.targets WHERE id = ANY(%s)",
                (target_ids,),
            )
            symbols = dict(cur.fetchall())
            for row in rows:
                if symbols.get(int(row["target_id"])) != row["human_symbol"]:
                    raise RuntimeError(f"Target mismatch in audit row: {row}")

            cur.execute(
                """
                SELECT subject_id, max(value_numeric)
                FROM preclin.evidence_score
                WHERE subject_type = 'target'
                  AND subject_id = ANY(%s)
                  AND dimension = 'impc_n_phenotypes'
                GROUP BY subject_id
                """,
                (target_ids,),
            )
            old_values = dict(cur.fetchall())
            write_ledger(rows, old_values)

            status_counts = {}
            numeric_counts = {"positive": 0, "zero": 0, "unknown": 0}
            for row in rows:
                status = resolved_status(row)
                status_counts[status] = status_counts.get(status, 0) + 1
                value = proposed_value(row)
                key = "unknown" if value is None else ("zero" if value == 0 else "positive")
                numeric_counts[key] += 1

            print(f"Audit SHA-256: {digest}")
            print(f"Status counts: {status_counts}")
            print(f"Numeric feature counts: {numeric_counts}")

            if args.dry_run:
                conn.rollback()
                print(f"Dry run; wrote ledger only: {LEDGER}")
                return

            dimensions = [
                (
                    "impc_phenotyping_status", "D_animal", "target", "text",
                    "DR24 mouse ortholog mapping and phenotyping eligibility status",
                    "impc",
                ),
                (
                    "impc_n_homozygous_procedures_tested", "D_animal", "target",
                    "count", "Distinct successful homozygous IMPC procedures in DR24",
                    "impc",
                ),
            ]
            execute_values(
                cur,
                """
                INSERT INTO preclin.evidence_dimension
                  (dimension, category, subject_type, data_type, description, source_primary)
                VALUES %s
                ON CONFLICT (dimension) DO UPDATE SET
                  description = EXCLUDED.description,
                  source_primary = EXCLUDED.source_primary
                """,
                dimensions,
            )

            cur.execute(
                """
                DELETE FROM preclin.evidence_score
                WHERE subject_type = 'target'
                  AND dimension = 'impc_n_phenotypes'
                  AND source = 'impc'
                  AND source_version = 'fill_zero_2026-08'
                  AND extracted_by = 'script:fill_gaps'
                """
            )
            deleted_synthetic = cur.rowcount

            cur.execute(
                """
                DELETE FROM preclin.evidence_score
                WHERE subject_type = 'target'
                  AND source = 'impc'
                  AND source_version = %s
                  AND extracted_by = %s
                  AND dimension IN (
                    'impc_n_phenotypes', 'impc_phenotyping_status',
                    'impc_n_homozygous_procedures_tested'
                  )
                """,
                (SOURCE_VERSION, EXTRACTED_BY),
            )

            evidence_rows = []
            for row in rows:
                target_id = int(row["target_id"])
                status = resolved_status(row)
                detail = json.dumps({
                    "release": SOURCE_VERSION,
                    "release_date": row["release_date"],
                    "audit_sha256": digest,
                    "mouse_symbols": row["mouse_symbols"].split("|")
                    if row["mouse_symbols"] else [],
                    "mgi_accession_ids": row["mgi_accession_ids"].split("|")
                    if row["mgi_accession_ids"] else [],
                    "eligibility_rule": (
                        "one mouse mapping; no significant MP term; phenotyping "
                        "available; >=13 successful homozygous procedures"
                    ),
                    "gene_api": "https://www.ebi.ac.uk/mi/impc/solr/gene/select",
                    "phenotype_api": (
                        "https://www.ebi.ac.uk/mi/impc/solr/"
                        "genotype-phenotype/select"
                    ),
                    "statistics_api": (
                        "https://www.ebi.ac.uk/mi/impc/solr/"
                        "statistical-result/select"
                    ),
                })
                evidence_rows.append((
                    "target", target_id, None, "impc_phenotyping_status",
                    "D_animal", None, status, None, detail, "impc",
                    SOURCE_VERSION, EXTRACTED_BY,
                ))
                procedures = int(row["homozygous_tested_procedures"])
                if procedures:
                    evidence_rows.append((
                        "target", target_id, None,
                        "impc_n_homozygous_procedures_tested", "D_animal",
                        float(procedures), None, None, detail, "impc",
                        SOURCE_VERSION, EXTRACTED_BY,
                    ))
                value = proposed_value(row)
                if value is not None:
                    evidence_rows.append((
                        "target", target_id, None, "impc_n_phenotypes",
                        "D_animal", value, None, None, detail, "impc",
                        SOURCE_VERSION, EXTRACTED_BY,
                    ))

            execute_values(
                cur,
                """
                INSERT INTO preclin.evidence_score
                  (subject_type, subject_id, subject_id2, dimension, category,
                   value_numeric, value_text, value_boolean, citation_details,
                   source, source_version, extracted_by)
                VALUES %s
                """,
                evidence_rows,
                template=(
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)"
                ),
                page_size=1000,
            )

            cur.execute(
                """
                INSERT INTO preclin.ingest_log
                  (source_file, target_table, rows_read, rows_inserted,
                   rows_skipped, rows_updated, finished_at, status, notes)
                VALUES (%s, 'preclin.evidence_score', %s, %s, 0, 0,
                        now(), 'completed', %s)
                """,
                (
                    str(INPUT.relative_to(ROOT)), len(rows), len(evidence_rows),
                    f"IMPC {SOURCE_VERSION}; sha256={digest}; removed "
                    f"{deleted_synthetic} synthetic zeros",
                ),
            )
        conn.commit()
        print(f"Removed synthetic zeros: {deleted_synthetic}")
        print(f"Inserted audited DR24 evidence rows: {len(evidence_rows)}")
        print(f"Wrote change ledger: {LEDGER}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
