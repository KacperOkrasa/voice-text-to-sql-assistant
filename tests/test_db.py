"""Check demo data integrity, schema retrieval and read-only execution."""

import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from data.seed import create_demo_database
from src.db import (
    execute_query, get_connection, get_database_summary, get_relationships, get_schema,
    get_table_columns, get_table_page, get_tables,
)
from src.security import UnsafeSQLError, validate_sql


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "demo.db"
        create_demo_database(self.db_path)

    def test_demo_counts_and_integrity(self) -> None:
        with closing(get_connection(self.db_path)) as connection:
            for table, expected in (("customers", 20), ("products", 12), ("orders", 60)):
                with self.subTest(table=table):
                    # Table names are fixed test constants, never user input.
                    count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    self.assertEqual(count, expected)

            item_count = connection.execute("SELECT COUNT(*) FROM order_items").fetchone()[0]
            self.assertGreaterEqual(item_count, 60)
            self.assertLessEqual(item_count, 240)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

            early_orders = connection.execute(
                """
                SELECT o.order_id
                FROM orders o JOIN customers c ON c.customer_id = o.customer_id
                WHERE o.order_date < c.registered_at
                """
            ).fetchall()
            self.assertEqual(early_orders, [])

    def test_schema_contains_tables_columns_and_relationships(self) -> None:
        schema = json.loads(get_schema(self.db_path))
        tables = {table["name"]: table for table in schema["tables"]}
        for table in ("customers", "products", "orders", "order_items"):
            self.assertIn(table, tables)
        self.assertTrue(any(column["name"] == "unit_price_cents" and column["type"] == "INTEGER"
                            for column in tables["order_items"]["columns"]))
        self.assertIn({"column": "customer_id", "reference_table": "customers",
                       "reference_column": "customer_id"}, tables["orders"]["foreign_keys"])

    def test_ai_schema_excludes_records_defaults_checks_and_view_literals(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("CREATE TABLE private_data (note TEXT DEFAULT 'default-secret' "
                               "CHECK(note != 'check-secret'))")
            connection.execute("INSERT INTO private_data VALUES ('row-secret')")
            connection.execute("CREATE VIEW private_view AS SELECT 'view-secret' AS note")
        schema = get_schema(self.db_path)
        for secret in ("default-secret", "check-secret", "row-secret", "view-secret"):
            self.assertNotIn(secret, schema)
        tables = {table["name"]: table for table in json.loads(schema)["tables"]}
        self.assertEqual(tables["private_view"]["kind"], "view")
        self.assertEqual(tables["private_data"]["columns"][0]["name"], "note")

    def test_summary_and_relationships_describe_the_demo(self) -> None:
        summary = get_database_summary(self.db_path)
        self.assertEqual((summary["tables"], summary["views"]), (4, 0))
        self.assertEqual(summary["size_bytes"], self.db_path.stat().st_size)
        relationships = get_relationships(self.db_path)
        self.assertEqual(len(relationships), 3)
        self.assertIn({"Tabela": "orders", "Kolumna": "customer_id", "Tabela docelowa": "customers",
                       "Kolumna docelowa": "customer_id"}, relationships)

    def test_connection_blocks_writes_even_without_validation(self) -> None:
        with closing(get_connection(self.db_path)) as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM customers")
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("CREATE TEMP TABLE unsafe (id INTEGER)")
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("REPLACE INTO products SELECT * FROM products")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0], 20)

    def test_missing_database_is_not_created(self) -> None:
        missing = Path(self.temp_dir.name) / "missing.db"
        with self.assertRaises(FileNotFoundError):
            get_connection(missing)
        self.assertFalse(missing.exists())

    def test_existing_database_is_not_overwritten(self) -> None:
        original = self.db_path.read_bytes()
        with self.assertRaises(FileExistsError):
            create_demo_database(self.db_path)
        self.assertEqual(self.db_path.read_bytes(), original)

    def test_seed_is_repeatable(self) -> None:
        second_path = Path(self.temp_dir.name) / "second.db"
        create_demo_database(second_path)
        with closing(get_connection(self.db_path)) as first:
            with closing(get_connection(second_path)) as second:
                self.assertEqual(list(first.iterdump()), list(second.iterdump()))

    def test_connection_handles_special_characters_in_path(self) -> None:
        path = Path(self.temp_dir.name) / "demo space ?#.db"
        create_demo_database(path)
        with closing(get_connection(path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 60)

    def test_monthly_revenue_query(self) -> None:
        query = validate_sql(
            """
            SELECT
                strftime('%Y-%m', o.order_date) AS month,
                ROUND(SUM(i.quantity * i.unit_price_cents) / 100.0, 2) AS revenue_pln
            FROM orders o
            JOIN order_items i ON i.order_id = o.order_id
            WHERE o.status = 'delivered'
            GROUP BY month
            ORDER BY month;
            """
        )
        with closing(get_connection(self.db_path)) as connection:
            rows = connection.execute(query).fetchall()
        self.assertEqual([row["month"] for row in rows], [f"2025-{month:02d}" for month in range(3, 9)])
        self.assertTrue(all(row["revenue_pln"] > 0 for row in rows))

    def test_execution_returns_column_names_and_bounded_rows(self) -> None:
        rows, columns = execute_query(
            "SELECT customer_id, city FROM customers ORDER BY customer_id",
            self.db_path, row_limit=3,
        )
        self.assertEqual(columns, ["customer_id", "city"])
        self.assertEqual([row["customer_id"] for row in rows], [1, 2, 3, 4])

    def test_execution_validates_before_running_sql(self) -> None:
        with self.assertRaises(UnsafeSQLError):
            execute_query("DELETE FROM orders", self.db_path)
        rows, _ = execute_query("SELECT COUNT(*) AS count FROM orders", self.db_path)
        self.assertEqual(rows[0]["count"], 60)

    def test_replace_function_changes_only_the_result(self) -> None:
        before, _ = execute_query("SELECT city FROM customers ORDER BY customer_id", self.db_path)
        rows, columns = execute_query(
            "SELECT replace('rower miejski', 'miejski', 'elektryczny') AS label", self.db_path,
        )
        self.assertEqual(columns, ["label"])
        self.assertEqual(rows[0]["label"], "rower elektryczny")
        with self.assertRaises(UnsafeSQLError):
            execute_query("WITH labels AS (SELECT replace('a', 'a', 'b')) "
                          "REPLACE INTO customers SELECT * FROM customers", self.db_path)
        after, _ = execute_query("SELECT city FROM customers ORDER BY customer_id", self.db_path)
        self.assertEqual([tuple(row) for row in after], [tuple(row) for row in before])

    def test_execution_interrupts_expensive_queries(self) -> None:
        query = """
            SELECT SUM(a.quantity * b.quantity)
            FROM order_items a CROSS JOIN order_items b
            CROSS JOIN order_items c CROSS JOIN order_items d
        """
        with self.assertRaisesRegex(sqlite3.OperationalError, "interrupted"):
            execute_query(query, self.db_path, timeout_seconds=0.002)

    def test_execution_rejects_invalid_limits(self) -> None:
        with self.assertRaises(ValueError):
            execute_query("SELECT 1", self.db_path, row_limit=0)
        with self.assertRaises(ValueError):
            execute_query("SELECT 1", self.db_path, timeout_seconds=0)

    def test_table_browser_lists_metadata_and_pages_without_modifying_data(self) -> None:
        self.assertEqual(get_tables(self.db_path), ["customers", "order_items", "orders", "products"])
        metadata = get_table_columns("orders", self.db_path)
        self.assertEqual(metadata[0]["Kolumna"], "order_id")
        self.assertEqual(metadata[0]["Klucz główny"], "Tak")
        rows, columns, count = get_table_page("orders", self.db_path, page=2)
        self.assertEqual(count, 60)
        self.assertEqual(len(rows), 10)
        self.assertIn("order_id", columns)
        self.assertEqual(rows[0]["order_id"], 51)

    def test_table_browser_quotes_names_and_rejects_unknown_identifiers(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute('CREATE TABLE "table "" name" ("column with spaces" TEXT)')
            connection.execute('INSERT INTO "table "" name" VALUES (?)', ("sample",))
            connection.execute('CREATE VIEW test_view AS SELECT * FROM "table "" name"')
        rows, columns, count = get_table_page('table " name', self.db_path)
        self.assertEqual((rows[0][0], columns, count), ("sample", ["column with spaces"], 1))
        self.assertIn("test_view", get_tables(self.db_path))
        with self.assertRaises(ValueError):
            get_table_page('orders; DROP TABLE orders', self.db_path)


if __name__ == "__main__":
    unittest.main()
