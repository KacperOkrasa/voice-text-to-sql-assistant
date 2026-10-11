"""Verify accepted analytics queries and rejected unsafe SQL inputs."""

import unittest

from src.security import UnsafeSQLError, is_sql_input, validate_sql


class SecurityTests(unittest.TestCase):
    def test_pasted_sql_is_recognized_in_comments_fences_and_wrappers(self) -> None:
        for text in (
            "SELECT 42", "\ufeff -- local secret\nSELECT 'secret' AS value",
            "/* local secret */ SELECT * FROM orders", "```sql\nSELECT 1\n```",
            "```\nSELECT 1\n```", "Wykonaj: SELECT 'secret'",
            "WITH totals AS (VALUES (1)) TABLE totals", "INSERT INTO orders VALUES (1)",
            "UPDATE orders SET status='secret'", "DELETE FROM orders",
            "DROP TABLE orders", "ALTER TABLE orders ADD note TEXT",
            "PRAGMA table_info(orders)", "EXPLAIN SELECT * FROM orders",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_sql_input(text))

    def test_natural_questions_and_short_followups_remain_allowed(self) -> None:
        for text in (
            "Ile jest zamówień?", "A tylko w sierpniu?", "Według przychodu",
            "With cancelled orders included, how much revenue did we earn?",
            "Update the analysis to include August", "Explain the monthly revenue",
            "Create a summary of sales", "Show order status by month",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_sql_input(text))

    def test_accepts_select_queries(self) -> None:
        queries = [
            "SELECT * FROM customers",
            "select count(*) from orders",
            "SELECT city, COUNT(*) AS customer_count FROM customers GROUP BY city",
            "SELECT p.name, SUM(i.quantity) FROM products p "
            "JOIN order_items i ON p.product_id = i.product_id GROUP BY p.name",
            "SELECT * FROM orders WHERE customer_id IN "
            "(SELECT customer_id FROM customers WHERE city = 'Warszawa')",
            "SELECT 1 UNION ALL SELECT 2",
            "SELECT 'updated' AS label",
            "SELECT 'DELETE; -- /*' AS label",
            "SELECT 'it''s safe' AS label",
            "WITH totals AS (SELECT 1) SELECT * FROM totals",
            "WITH RECURSIVE nums(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM nums WHERE n < 5) SELECT * FROM nums",
        ]
        for query in queries:
            with self.subTest(query=query):
                self.assertEqual(validate_sql(query), query)

    def test_trims_whitespace_and_one_trailing_semicolon(self) -> None:
        self.assertEqual(validate_sql(" \n SELECT 1; \n"), "SELECT 1")

    def test_accepts_replace_as_a_read_only_function(self) -> None:
        queries = [
            "SELECT replace('rower miejski', 'miejski', 'elektryczny') AS label",
            "SELECT RePlAcE \n ('DROP', 'DROP', 'DELETE') AS label",
            "WITH labels AS (SELECT replace(city, 'a', 'A') AS city FROM customers) SELECT * FROM labels",
            "SELECT replace(replace('abc', 'a', 'b'), 'b', 'c')",
        ]
        for query in queries:
            with self.subTest(query=query):
                self.assertEqual(validate_sql(query), query)

    def test_replace_function_does_not_hide_forbidden_statements(self) -> None:
        queries = [
            "WITH labels AS (SELECT replace('a', 'a', 'b')) REPLACE INTO products SELECT * FROM products",
            "SELECT replace('a', 'a', 'b') UNION UPDATE orders SET status = 'x'",
            "SELECT replace('a', 'a', 'b'), load_extension('file')",
            "SELECT replace('a', 'a', 'b'); REPLACE INTO products SELECT * FROM products",
        ]
        for query in queries:
            with self.subTest(query=query):
                with self.assertRaises(UnsafeSQLError):
                    validate_sql(query)

    def test_rejects_modifying_and_administrative_statements(self) -> None:
        queries = [
            "DROP TABLE customers",
            "delete FROM customers",
            "uPdAtE products SET price_cents = 1",
            "INSERT INTO customers VALUES (1, 'A', 'a@example.com', 'X', '2025-01-01')",
            "ALTER TABLE orders ADD COLUMN note TEXT",
            "CREATE TABLE other (id INTEGER)",
            "REPLACE INTO products VALUES (1, 'A', 'B', 1)",
            "PRAGMA query_only = OFF",
            "ATTACH DATABASE 'other.db' AS other",
            "VACUUM INTO 'copy.db'",
            "WITH totals AS (SELECT 1) DELETE FROM orders",
            "EXPLAIN SELECT * FROM customers",
        ]
        for query in queries:
            with self.subTest(query=query):
                with self.assertRaises(UnsafeSQLError):
                    validate_sql(query)

    def test_rejects_multiple_statements(self) -> None:
        for query in ("SELECT 1; DELETE FROM orders", "SELECT 1; SELECT 2", "SELECT 1;;"):
            with self.subTest(query=query):
                with self.assertRaises(UnsafeSQLError):
                    validate_sql(query)

    def test_rejects_comments(self) -> None:
        for query in ("SELECT 1 -- comment", "SELECT /* comment */ 1", "/* comment */ SELECT 1"):
            with self.subTest(query=query):
                with self.assertRaises(UnsafeSQLError):
                    validate_sql(query)

    def test_rejects_forbidden_keywords_inside_select(self) -> None:
        for query in ("SELECT load_extension('file')", "SELECT 1 UNION UPDATE orders SET status = 'x'"):
            with self.subTest(query=query):
                with self.assertRaises(UnsafeSQLError):
                    validate_sql(query)

    def test_rejects_unclosed_literals_and_identifiers(self) -> None:
        for query in ("SELECT 'hello", 'SELECT "hello', "SELECT [hello", "SELECT `hello"):
            with self.subTest(query=query):
                with self.assertRaises(UnsafeSQLError):
                    validate_sql(query)

    def test_rejects_empty_non_text_and_non_select_inputs(self) -> None:
        for query in ("", " \n ", None, 123, b"SELECT 1", "SELECTED 1", "```sql\nSELECT 1\n```", "SELECT \x00", "SELECT " + "1" * 10001):
            with self.subTest(query=query):
                with self.assertRaises(UnsafeSQLError):
                    validate_sql(query)


if __name__ == "__main__":
    unittest.main()
