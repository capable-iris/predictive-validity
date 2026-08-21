"""Populate study-level GWAS evidence dates from an official Catalog release.

The GWAS Catalog study download contains one publication date per study
accession. Only accessions represented in ``public.gwas_associations`` are
stored. No publication text is downloaded and no LLM is called.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import tempfile
import urllib.request
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, execute_values


DEFAULT_URL = (
    "https://ftp.ebi.ac.uk/pub/databases/gwas/releases/latest/"
    "gwas-catalog-download-studies-v1.0.3.1.txt"
)


def parse_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def download_catalog(url: str, destination: Path) -> tuple[str, str | None]:
    digest = hashlib.sha256()
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "predictive-validity/1.0 (GWAS date ingest)"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        last_modified = response.headers.get("Last-Modified")
        with destination.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                output.write(chunk)
    return digest.hexdigest(), last_modified


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def infer_source_version(last_modified: str | None) -> str:
    if not last_modified:
        raise ValueError("source version is required when Last-Modified is unavailable")
    modified = parsedate_to_datetime(last_modified)
    return f"r{modified.date().isoformat()}"


def required_studies(cur) -> dict[str, str]:
    cur.execute(
        """
        SELECT study_accession, min(study_pmid)
        FROM public.gwas_associations
        WHERE study_accession IS NOT NULL AND btrim(study_accession) <> ''
        GROUP BY study_accession
        """
    )
    return {str(accession): str(pmid) for accession, pmid in cur.fetchall()}


def catalog_rows(path: Path, required: dict[str, str]) -> tuple[list[tuple], dict]:
    matched: dict[str, tuple] = {}
    malformed_dates = 0
    pmid_mismatches = 0
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        required_columns = {"STUDY ACCESSION", "PUBMED ID", "DATE"}
        missing_columns = required_columns - set(reader.fieldnames or [])
        if missing_columns:
            raise ValueError(
                f"GWAS Catalog download lacks columns: {sorted(missing_columns)}"
            )
        for row in reader:
            accession = (row.get("STUDY ACCESSION") or "").strip()
            if accession not in required:
                continue
            try:
                publication_date = parse_date(row.get("DATE") or "")
                catalog_added_date = parse_date(row.get("DATE ADDED TO CATALOG") or "")
            except ValueError:
                malformed_dates += 1
                continue
            if publication_date is None:
                malformed_dates += 1
                continue
            pmid = (row.get("PUBMED ID") or "").strip()
            if pmid != required[accession]:
                pmid_mismatches += 1
            value = (
                accession,
                pmid or required[accession],
                publication_date,
                catalog_added_date,
                "gwas_catalog_publication_date",
                "day",
                f"https://www.ebi.ac.uk/gwas/studies/{accession}",
                Json({
                    "catalog_trait": row.get("DISEASE/TRAIT") or None,
                    "submission_date": row.get("SUBMISSION DATE") or None,
                }),
            )
            previous = matched.get(accession)
            if previous is not None and previous[:4] != value[:4]:
                raise ValueError(f"conflicting Catalog rows for {accession}")
            matched[accession] = value
    return list(matched.values()), {
        "required_studies": len(required),
        "matched_studies": len(matched),
        "missing_studies": len(required) - len(matched),
        "malformed_dates": malformed_dates,
        "pmid_mismatches": pmid_mismatches,
    }


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--input", type=Path, help="Use an existing Catalog TSV")
    parser.add_argument("--source-version", help="Catalog release, e.g. r2026-08-03")
    parser.add_argument("--dry-run", action="store_true", help="Validate and roll back")
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Commit even when database accessions are absent from the release",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    temporary_dir = None
    last_modified = None
    try:
        if args.input:
            source_path = args.input
            digest = file_sha256(source_path)
        else:
            temporary_dir = tempfile.TemporaryDirectory(prefix="gwas-study-dates-")
            source_path = Path(temporary_dir.name) / "studies.tsv"
            digest, last_modified = download_catalog(args.url, source_path)
        source_version = args.source_version or infer_source_version(last_modified)

        conn = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with conn.cursor() as cur:
                required = required_studies(cur)
                rows, stats = catalog_rows(source_path, required)
                if stats["missing_studies"] and not args.allow_missing:
                    raise RuntimeError(
                        f"Catalog release is missing {stats['missing_studies']} of "
                        f"{stats['required_studies']} required study accessions"
                    )
                cur.execute(
                    """
                    INSERT INTO preclin.evidence_source_release
                      (source_name, source_version, source_url, content_sha256, metadata)
                    VALUES ('gwas_catalog_studies', %s, %s, %s, %s)
                    ON CONFLICT (source_name, source_version, content_sha256)
                    DO UPDATE SET retrieved_at = preclin.evidence_source_release.retrieved_at
                    RETURNING source_release_id
                    """,
                    (
                        source_version,
                        args.url if not args.input else str(args.input.resolve()),
                        digest,
                        Json({
                            "last_modified": last_modified,
                            "format": "gwas-catalog-download-studies-v1.0.3.1",
                            **stats,
                        }),
                    ),
                )
                release_id = int(cur.fetchone()[0])
                insert_rows = [(release_id, *row) for row in rows]
                execute_values(
                    cur,
                    """
                    INSERT INTO preclin.gwas_study_date
                      (source_release_id, study_accession, study_pmid,
                       evidence_available_date, catalog_added_date, date_basis,
                       date_precision, source_url, metadata)
                    VALUES %s
                    ON CONFLICT (source_release_id, study_accession) DO UPDATE SET
                      study_pmid = EXCLUDED.study_pmid,
                      evidence_available_date = EXCLUDED.evidence_available_date,
                      catalog_added_date = EXCLUDED.catalog_added_date,
                      date_basis = EXCLUDED.date_basis,
                      date_precision = EXCLUDED.date_precision,
                      source_url = EXCLUDED.source_url,
                      metadata = EXCLUDED.metadata
                    """,
                    insert_rows,
                    page_size=1000,
                )
                cur.execute(
                    """
                    SELECT count(*), min(evidence_available_date),
                           max(evidence_available_date)
                    FROM preclin.gwas_study_date
                    WHERE source_release_id = %s
                    """,
                    (release_id,),
                )
                stored, earliest, latest = cur.fetchone()
                if args.dry_run:
                    conn.rollback()
                    disposition = "validated; rolled back"
                else:
                    conn.commit()
                    disposition = "committed"
                print(
                    json.dumps(
                        {
                            **stats,
                            "stored_studies": stored,
                            "earliest_date": earliest.isoformat() if earliest else None,
                            "latest_date": latest.isoformat() if latest else None,
                            "source_version": source_version,
                            "content_sha256": digest,
                            "disposition": disposition,
                        },
                        sort_keys=True,
                    )
                )
        finally:
            conn.close()
    finally:
        if temporary_dir is not None:
            temporary_dir.cleanup()


if __name__ == "__main__":
    main()
