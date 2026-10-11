"""Check financial aggregation using a small fixture with many-to-many join traps."""

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from src.analysis import analyze_question
from src.db import execute_query


BALANCE_SQL = (Path(__file__).resolve().parent.parent / "data" /
               "rowerownia_monthly_balance.sql").read_text(encoding="utf-8")


class MonthlyBalanceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db_path = Path(temporary.name) / "rentals.db"
        with sqlite3.connect(self.db_path) as connection:
            connection.executescript("""
                CREATE TABLE rentals (
                    rental_id INTEGER PRIMARY KEY, bike_id INTEGER,
                    started_at TEXT, charge_cents INTEGER
                );
                CREATE TABLE service_records (
                    service_id INTEGER PRIMARY KEY, bike_id INTEGER,
                    service_date TEXT, cost_cents INTEGER
                );
                INSERT INTO rentals VALUES
                    (1, 1, '2026-07-04 10:00:00', 1000),
                    (2, 1, '2026-07-08 10:00:00', 1000),
                    (3, 2, '2026-07-10 10:00:00', 500),
                    (4, 2, '2026-08-01 10:00:00', 2000);
                INSERT INTO service_records VALUES
                    (1, 1, '2026-07-03', 300),
                    (2, 1, '2026-07-07', 300),
                    (3, 3, '2026-07-11', 400),
                    (4, 3, '2026-09-01', 800);
            """)

    def test_separate_aggregation_counts_equal_amounts_and_unused_bike_costs(self) -> None:
        rows, _ = execute_query(BALANCE_SQL, self.db_path)
        # Both 10 PLN rentals and both 3 PLN repairs are separate transactions.
        # The additional 4 PLN repair belongs to a bike without any rentals.
        self.assertEqual(tuple(rows[0]), ("2026-07", 25.0, 10.0, 15.0))

    def test_months_with_only_revenue_or_only_costs_are_kept(self) -> None:
        rows, _ = execute_query(BALANCE_SQL, self.db_path)
        self.assertEqual([tuple(row) for row in rows[1:]], [
            ("2026-08", 20.0, 0.0, 20.0),
            ("2026-09", 0.0, 8.0, -8.0),
        ])

    def test_original_join_reproduces_inflated_totals(self) -> None:
        query = """
            SELECT strftime('%Y-%m', r.started_at) AS month,
                   SUM(r.charge_cents) / 100.0 AS revenue_pln,
                   COALESCE(SUM(s.cost_cents), 0) / 100.0 AS service_cost_pln
            FROM rentals r
            LEFT JOIN service_records s ON r.bike_id = s.bike_id
                AND strftime('%Y-%m', r.started_at) = strftime('%Y-%m', s.service_date)
            GROUP BY month ORDER BY month
        """
        rows, _ = execute_query(query, self.db_path)
        self.assertEqual(tuple(rows[0]), ("2026-07", 45.0, 12.0))
        correct_rows, _ = execute_query(BALANCE_SQL, self.db_path)
        self.assertNotEqual(tuple(rows[0]), tuple(correct_rows[0])[:3])

    def test_correct_response_runs_through_analysis_without_real_api_or_key(self) -> None:
        reply = SimpleNamespace(
            status="completed", output=[],
            output_text=json.dumps({"sql": BALANCE_SQL, "clarification": None}),
            usage=SimpleNamespace(input_tokens=1500, output_tokens=400),
        )
        with patch("src.ai.api_settings", return_value={"api_key": "test-only", "sql_model": "gpt-4o-mini"}), \
                patch("src.ai.create_client") as client:
            request = client.return_value.__enter__.return_value.responses.create
            request.return_value = reply
            entry = analyze_question("Pokaż miesięczny przychód pomniejszony o koszty serwisu.",
                                     str(self.db_path))
        self.assertIsNone(entry["error"])
        self.assertEqual(list(entry["frame"].itertuples(index=False, name=None)), [
            ("2026-07", 25.0, 10.0, 15.0),
            ("2026-08", 20.0, 0.0, 20.0),
            ("2026-09", 0.0, 8.0, -8.0),
        ])
        self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
