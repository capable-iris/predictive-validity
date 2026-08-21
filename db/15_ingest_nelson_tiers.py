"""Ingest versioned cohort-wide Nelson-tier results without a full reingest."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg2

from nelson_tier_io import prepare_database_rows, upsert_database_rows


DEFAULT_DIRECTORY = Path(__file__).resolve().parents[1] / "data" / "target_evidence"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate files and IDs, but roll back instead of ingesting",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        cur = conn.cursor()
        paths, rows = prepare_database_rows(cur, args.directory)
        print(f"Validated {len(rows)} rows from {len(paths)} result files")
        if args.dry_run:
            conn.rollback()
            print("Dry run complete; no database changes were committed")
            return
        upsert_database_rows(cur, rows)
        conn.commit()
        print(f"Ingested {len(rows)} cohort-wide Nelson-tier rows")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
