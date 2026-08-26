"""Apply and audit numbered database migrations.

The core bootstrap files (01-13) are intentionally outside this runner.
Numbered SQL and Python migrations from 14 onward are discovered directly from
``db/``. Applied file hashes are immutable; changing a recorded file produces
a hard drift error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg2
from psycopg2.extras import Json


ROOT = Path(__file__).resolve().parents[1]
DB_DIR = Path(__file__).resolve().parent
FIRST_TRACKED_MIGRATION = 14
LEDGER_MIGRATION = 26
MIGRATION_RE = re.compile(r"^(?P<number>[0-9]{2})_(?P<name>.+)[.](?P<kind>sql|py)$")
ADVISORY_LOCK_NAME = "predictive-validity:preclin.schema_migration"


@dataclass(frozen=True)
class Migration:
    number: int
    name: str
    kind: str
    path: Path
    sha256: str

    @property
    def filename(self) -> str:
        return self.path.name


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def discover_migrations(db_dir: Path = DB_DIR) -> list[Migration]:
    migrations = []
    for path in sorted(db_dir.iterdir()):
        match = MIGRATION_RE.fullmatch(path.name)
        if match is None:
            continue
        number = int(match.group("number"))
        if number < FIRST_TRACKED_MIGRATION:
            continue
        migrations.append(
            Migration(
                number=number,
                name=match.group("name"),
                kind="python" if match.group("kind") == "py" else "sql",
                path=path,
                sha256=file_sha256(path),
            )
        )
    duplicates = {
        number for number in (m.number for m in migrations)
        if sum(item.number == number for item in migrations) > 1
    }
    if duplicates:
        raise RuntimeError(f"Duplicate migration numbers: {sorted(duplicates)}")
    if not any(m.number == LEDGER_MIGRATION for m in migrations):
        raise RuntimeError(f"Ledger migration {LEDGER_MIGRATION} is missing")
    return sorted(migrations, key=lambda migration: migration.number)


def direct_database_url(url: str) -> str:
    """Use the direct Neon endpoint without ever logging the credential."""
    from urllib.parse import urlsplit, urlunsplit

    parsed = urlsplit(url)
    if "-pooler." not in (parsed.hostname or ""):
        return url
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc.replace("-pooler.", ".", 1),
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


def git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value) else None


def ledger_exists(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('preclin.schema_migration') IS NOT NULL")
        return bool(cur.fetchone()[0])


def load_ledger(conn) -> dict[int, dict]:
    if not ledger_exists(conn):
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT migration_number, migration_name, filename, file_sha256,
                   migration_kind, execution_mode, applied_at, applied_by,
                   git_commit, details
            FROM preclin.schema_migration
            ORDER BY migration_number
            """
        )
        fields = [description.name for description in cur.description]
        return {row[0]: dict(zip(fields, row)) for row in cur.fetchall()}


def migration_state(migration: Migration, ledger_row: dict | None) -> str:
    if ledger_row is None:
        return "pending"
    if (
        ledger_row["filename"] != migration.filename
        or ledger_row["migration_name"] != migration.name
        or ledger_row["migration_kind"] != migration.kind
        or ledger_row["file_sha256"] != migration.sha256
    ):
        return "drift"
    return ledger_row["execution_mode"]


def record_migration(
    conn,
    migration: Migration,
    execution_mode: str,
    details: dict | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO preclin.schema_migration
              (migration_number, migration_name, filename, file_sha256,
               migration_kind, execution_mode, git_commit, details)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                migration.number,
                migration.name,
                migration.filename,
                migration.sha256,
                migration.kind,
                execution_mode,
                git_commit(),
                Json(details or {}),
            ),
        )
    conn.commit()


def execute_sql(conn, migration: Migration) -> None:
    with conn.cursor() as cur:
        cur.execute(migration.path.read_text())
    conn.commit()


def execute_python(migration: Migration) -> None:
    result = subprocess.run(
        [sys.executable, str(migration.path)],
        cwd=ROOT,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Python migration {migration.filename} failed with exit code "
            f"{result.returncode}"
        )


def ensure_ledger(conn, migrations: list[Migration]) -> None:
    migration = next(m for m in migrations if m.number == LEDGER_MIGRATION)
    if ledger_exists(conn):
        state = migration_state(migration, load_ledger(conn).get(LEDGER_MIGRATION))
        if state == "drift":
            raise RuntimeError(f"Checksum drift for {migration.filename}")
        if state != "pending":
            return
        print(f"registering ledger migration {migration.filename}")
        execute_sql(conn, migration)
        record_migration(
            conn,
            migration,
            "applied",
            {"runner": "db/migrate.py", "bootstrap": True},
        )
        return
    print(f"initializing ledger with {migration.filename}")
    execute_sql(conn, migration)
    record_migration(
        conn,
        migration,
        "applied",
        {"runner": "db/migrate.py", "bootstrap": True},
    )


def print_status(conn, migrations: list[Migration]) -> int:
    rows = load_ledger(conn)
    if not ledger_exists(conn):
        print("ledger: absent (run `db/migrate.py init`)")
    drift = False
    for migration in migrations:
        state = migration_state(migration, rows.get(migration.number))
        drift |= state == "drift"
        print(
            f"{migration.number:02d} {state:8s} {migration.sha256[:12]} "
            f"{migration.filename}"
        )
    unknown = sorted(set(rows) - {migration.number for migration in migrations})
    for number in unknown:
        drift = True
        print(f"{number:02d} drift    {'-' * 12} ledger row has no local file")
    return 2 if drift else 0


def apply_pending(conn, migrations: list[Migration], through: int | None) -> None:
    ensure_ledger(conn, migrations)
    rows = load_ledger(conn)
    for migration in migrations:
        if migration.number == LEDGER_MIGRATION:
            continue
        if through is not None and migration.number > through:
            continue
        state = migration_state(migration, rows.get(migration.number))
        if state == "drift":
            raise RuntimeError(f"Checksum drift for {migration.filename}")
        if state != "pending":
            continue
        print(f"applying {migration.filename}", flush=True)
        if migration.kind == "sql":
            execute_sql(conn, migration)
        else:
            execute_python(migration)
        record_migration(
            conn,
            migration,
            "applied",
            {"runner": "db/migrate.py"},
        )
        rows = load_ledger(conn)


def baseline_existing(
    conn,
    migrations: list[Migration],
    through: int,
    reason: str,
) -> None:
    ensure_ledger(conn, migrations)
    rows = load_ledger(conn)
    for migration in migrations:
        if migration.number == LEDGER_MIGRATION or migration.number > through:
            continue
        state = migration_state(migration, rows.get(migration.number))
        if state == "drift":
            raise RuntimeError(f"Checksum drift for {migration.filename}")
        if state != "pending":
            continue
        record_migration(
            conn,
            migration,
            "baseline",
            {"runner": "db/migrate.py", "reason": reason},
        )
        print(f"baselined {migration.filename}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    subparsers.add_parser("init")

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--through", type=int)

    baseline_parser = subparsers.add_parser("baseline")
    baseline_parser.add_argument("--through", type=int, required=True)
    baseline_parser.add_argument("--reason", required=True)
    baseline_parser.add_argument("--yes", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    migrations = discover_migrations()
    database_url = direct_database_url(os.environ["DATABASE_URL"])
    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(hashtext(%s))", (ADVISORY_LOCK_NAME,))
        if args.command == "status":
            raise SystemExit(print_status(conn, migrations))
        if args.command == "init":
            ensure_ledger(conn, migrations)
            return
        if args.command == "apply":
            apply_pending(conn, migrations, args.through)
            return
        if args.command == "baseline":
            if not args.yes:
                raise RuntimeError(
                    "Baseline records migrations without executing them; pass --yes "
                    "after verifying this database's current state"
                )
            baseline_existing(conn, migrations, args.through, args.reason)
            return
        raise AssertionError(args.command)
    finally:
        try:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (ADVISORY_LOCK_NAME,))
            conn.commit()
        finally:
            conn.close()


if __name__ == "__main__":
    main()
