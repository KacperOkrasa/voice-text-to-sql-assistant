"""Check SQLite imports, persistence and read-only analysis of custom data."""

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.db import execute_query
from src.storage import list_imported_databases
from src.uploads import import_database


class UploadTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        for target, value in (
            ("src.storage.DEFAULT_STORAGE_PATH", self.directory / "history.db"),
            ("src.uploads.UPLOAD_DIRECTORY", self.directory / "uploads"),
        ):
            mocked = patch(target, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        source = self.directory / "source.db"
        with sqlite3.connect(source) as connection:
            connection.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, amount REAL)")
            connection.execute("INSERT INTO sales VALUES (1, 123.45)")
        self.content = source.read_bytes()

    def test_import_is_durable_deduplicated_and_cannot_write(self) -> None:
        imported = import_database("../../sprzedaż.sqlite", self.content)
        again = import_database("sprzedaż.sqlite", self.content)
        self.assertEqual(imported["id"], again["id"])
        self.assertEqual(imported["name"], "sprzedaż.sqlite")
        self.assertEqual(Path(imported["path"]).parent, self.directory / "uploads")
        self.assertEqual(len(list_imported_databases()), 1)
        original = Path(imported["path"]).read_bytes()
        rows, _ = execute_query("SELECT amount FROM sales", imported["path"])
        self.assertEqual(rows[0][0], 123.45)
        with self.assertRaises(ValueError):
            execute_query("DELETE FROM sales", imported["path"])
        self.assertEqual(Path(imported["path"]).read_bytes(), original)

    def test_invalid_corrupt_empty_and_oversized_files_are_not_registered(self) -> None:
        with self.assertRaises(ValueError):
            import_database("bad.db", b"not sqlite")
        with self.assertRaises(ValueError):
            import_database("bad.db", b"SQLite format 3\x00" + b"x" * 500)
        empty = self.directory / "empty.db"
        with sqlite3.connect(empty) as connection:
            connection.execute("PRAGMA user_version = 1")
        with self.assertRaises(ValueError):
            import_database("empty.db", empty.read_bytes())
        with patch("src.uploads.MAX_DATABASE_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "50 MB"):
                import_database("large.db", self.content)
        self.assertEqual(list_imported_databases(), [])
        self.assertEqual(list((self.directory / "uploads").glob("*.db")), [])

    def test_corrupt_data_page_with_readable_catalog_is_rejected(self):
        source = self.directory / "source.db"
        with sqlite3.connect(source) as connection:
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            root_page = connection.execute("SELECT rootpage FROM sqlite_master WHERE name = 'sales'").fetchone()[0]
        broken = bytearray(self.content)
        broken[(root_page - 1) * page_size] = 0xff
        with self.assertRaises(ValueError):
            import_database("corrupt.db", bytes(broken))
        self.assertFalse(list_imported_databases())
        self.assertFalse(list((self.directory / "uploads").glob("*.db")))

    def test_invalid_view_is_rejected_before_registration(self):
        source = self.directory / "source.db"
        with sqlite3.connect(source) as connection:
            connection.execute("CREATE VIEW broken AS SELECT * FROM missing_table")
        with self.assertRaises(ValueError):
            import_database("broken_view.db", source.read_bytes())
        self.assertFalse(list_imported_databases())
        self.assertFalse(list((self.directory / "uploads").glob("*.db")))

    def test_valid_view_is_accepted_and_validation_has_a_deadline(self):
        from src.db import validate_database
        source = self.directory / "source.db"
        with sqlite3.connect(source) as connection:
            connection.execute("CREATE VIEW totals AS SELECT SUM(amount) AS amount FROM sales")
        self.assertTrue(import_database("valid.db", source.read_bytes())["id"])
        with self.assertRaises((ValueError, sqlite3.OperationalError)):
            validate_database(source, timeout_seconds=0)



if __name__ == "__main__":
    unittest.main()
