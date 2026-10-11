"""Regression checks for precise SQLite values and bounded result payloads."""

import csv
import io
import json
import math
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from src.analysis import analyze_question, load_result, new_entry
from src.db import execute_query, get_table_page
from src.presentation import browser_frame, export_csv
from src.results import ResultLimitError, result_frame
from src.storage import decode_entry, encode_entry
from tests.test_ai import response


class ResultTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "test.db"
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TABLE sample (id INTEGER, text TEXT)")

    def test_large_integer_null_survives_sql_history_browser_and_csv(self):
        entry = new_entry("Duże ID", "SELECT 9223372036854775807 AS id UNION ALL SELECT NULL")
        load_result(entry, str(self.path))
        self.assertEqual(entry["frame"].iloc[0, 0], 9223372036854775807)
        self.assertEqual(str(entry["frame"].dtypes.iloc[0]), "Int64")
        restored = decode_entry(encode_entry(entry), 1)
        self.assertEqual(restored["frame"].iloc[0, 0], 9223372036854775807)
        self.assertTrue(pd.isna(restored["frame"].iloc[1, 0]))
        self.assertEqual(browser_frame(restored["frame"]).iloc[0, 0], "9223372036854775807")
        raw = list(csv.reader(io.StringIO(export_csv(restored["frame"], False).decode("utf-8-sig"))))
        sheet = list(csv.reader(io.StringIO(export_csv(restored["frame"]).decode("utf-8-sig"))))
        self.assertEqual(raw[1][0], "9223372036854775807")
        self.assertEqual(sheet[1][0], "'9223372036854775807")

    def test_mixed_sqlite_types_and_precise_float_roundtrip(self):
        frame = result_frame([(9223372036854775807,), (1.2345678901234567,), ("001",), (None,)], ["mixed"])
        restored = decode_entry(encode_entry({"frame": frame}), 1)["frame"]
        self.assertEqual(restored.iloc[0, 0], 9223372036854775807)
        self.assertIsInstance(restored.iloc[0, 0], int)
        self.assertEqual(restored.iloc[1, 0], 1.2345678901234567)
        self.assertEqual(restored.iloc[2, 0], "001")
        self.assertEqual(browser_frame(restored).iloc[0, 0], "9223372036854775807")

    def test_infinity_is_preserved_distinct_from_null(self):
        rows, columns = execute_query("SELECT 1e999 AS value UNION ALL SELECT NULL", self.path)
        frame = result_frame(rows, columns)
        encoded = encode_entry({"frame": frame})
        self.assertNotIn("Infinity", encoded)
        restored = decode_entry(encoded, 1)["frame"]
        self.assertEqual(restored.iloc[0, 0], math.inf)
        self.assertTrue(pd.isna(restored.iloc[1, 0]))

    def test_legacy_snapshot_remains_readable_without_initial_float_inference(self):
        payload = json.dumps({"frame": json.dumps({"columns": ["id"],
                            "data": [[9223372036854775807], [None]]}), "dtypes": ["Int64"]})
        restored = decode_entry(payload, 1)
        self.assertEqual(restored["frame"].iloc[0, 0], 9223372036854775807)

    def test_large_cells_are_rejected_by_sqlite_or_result_budget(self):
        with self.assertRaises(sqlite3.DataError):
            execute_query("SELECT zeroblob(2000000)", self.path)
        with self.assertRaises(ResultLimitError):
            execute_query("SELECT zeroblob(40000)", self.path)
        with self.assertRaises(ResultLimitError):
            execute_query("SELECT printf('%70000s', 'x')", self.path)

    def test_total_bytes_and_width_are_bounded(self):
        with sqlite3.connect(self.path) as connection:
            connection.executemany("INSERT INTO sample VALUES (?, ?)", [(i, "x" * 60000) for i in range(75)])
        with self.assertRaises(ResultLimitError):
            execute_query("SELECT text FROM sample", self.path)
        with self.assertRaises(ResultLimitError):
            execute_query("SELECT " + ", ".join(f"1 AS c{i}" for i in range(101)), self.path)
        with self.assertRaises(ResultLimitError):
            get_table_page("sample", self.path, page_size=100)

    def test_browser_rejects_oversized_cell_too(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute("INSERT INTO sample VALUES (1, ?)", ("x" * 70000,))
        with self.assertRaises(ResultLimitError):
            get_table_page("sample", self.path)

    def test_limits_do_not_trigger_paid_repair(self):
        with patch("src.ai.api_settings", return_value={"api_key": "test-only", "sql_model": "gpt-4o-mini"}), \
                patch("src.ai.create_client") as client:
            request = client.return_value.__enter__.return_value.responses.create
            request.return_value = response("SELECT zeroblob(40000)")
            entry = analyze_question("Pokaż dane", str(self.path))
        self.assertEqual(request.call_count, 1)
        self.assertEqual(entry["error_reason"], "result_limit")
        self.assertTrue(entry["frame"].empty)

    def test_large_cents_are_exact_in_display_and_both_exports(self):
        from src.presentation import style_table
        frame = result_frame([(9223372036854775807,), (None,)], ["price_cents"])
        shown = style_table(frame)
        self.assertEqual(shown.data.iloc[0, 0], "92233720368547758.07")
        sheet = list(csv.reader(io.StringIO(export_csv(frame).decode("utf-8-sig"))))
        self.assertEqual(sheet[1][0], "'92233720368547758.07")
        raw = list(csv.reader(io.StringIO(export_csv(frame, False).decode("utf-8-sig"))))
        self.assertEqual(raw[1][0], "9223372036854775807")



if __name__ == "__main__":
    unittest.main()
