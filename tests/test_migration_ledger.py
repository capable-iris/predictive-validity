import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "db" / "migrate.py"
SPEC = importlib.util.spec_from_file_location("migration_runner", MODULE_PATH)
migrate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = migrate
SPEC.loader.exec_module(migrate)


class MigrationLedgerTests(unittest.TestCase):
    def test_discovers_numbered_sql_and_python_migrations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "14_one.py").write_text("print('one')\n")
            (root / "25_two.sql").write_text("SELECT 2;\n")
            (root / "26_migration_ledger.sql").write_text("SELECT 3;\n")
            (root / "helper.py").write_text("pass\n")

            migrations = migrate.discover_migrations(root)

        self.assertEqual([item.number for item in migrations], [14, 25, 26])
        self.assertEqual([item.kind for item in migrations], ["python", "sql", "sql"])

    def test_duplicate_migration_numbers_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "26_migration_ledger.sql").write_text("SELECT 1;\n")
            (root / "26_duplicate.py").write_text("pass\n")
            with self.assertRaisesRegex(RuntimeError, "Duplicate migration numbers"):
                migrate.discover_migrations(root)

    def test_changed_hash_is_drift(self):
        migration = migrate.Migration(
            number=27,
            name="example",
            kind="sql",
            path=Path("27_example.sql"),
            sha256="a" * 64,
        )
        row = {
            "filename": migration.filename,
            "migration_name": migration.name,
            "migration_kind": migration.kind,
            "file_sha256": "b" * 64,
            "execution_mode": "applied",
        }
        self.assertEqual(migrate.migration_state(migration, row), "drift")

    def test_matching_baseline_is_preserved(self):
        migration = migrate.Migration(
            number=27,
            name="example",
            kind="sql",
            path=Path("27_example.sql"),
            sha256="a" * 64,
        )
        row = {
            "filename": migration.filename,
            "migration_name": migration.name,
            "migration_kind": migration.kind,
            "file_sha256": migration.sha256,
            "execution_mode": "baseline",
        }
        self.assertEqual(migrate.migration_state(migration, row), "baseline")


if __name__ == "__main__":
    unittest.main()
