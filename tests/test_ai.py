"""Verify payload privacy, bounded calls and API errors without real requests."""

import json
import sqlite3
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from dotenv import dotenv_values
from openai import AuthenticationError

from src.ai import AIError, api_configured, api_settings, create_client, generate_sql
from src.analysis import analyze_question, get_clarification_context, get_previous_analysis
from src.db import execute_query, get_schema


def response(sql: str | None = "SELECT COUNT(*) AS total FROM customers", clarification: str | None = None,
             status: str = "completed", reason: str | None = None):
    """Provide a minimal SDK-shaped response for all API tests."""
    return SimpleNamespace(
        status=status, incomplete_details=SimpleNamespace(reason=reason),
        output=[], output_text=json.dumps({"sql": sql, "clarification": clarification}),
        usage=SimpleNamespace(input_tokens=2000, output_tokens=300),
    )


class AITests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db_path = Path(temporary.name) / "private.db"
        with sqlite3.connect(self.db_path) as connection:
            connection.executescript("""
                CREATE TABLE customers (id INTEGER PRIMARY KEY, secret TEXT DEFAULT 'DEFAULT_SECRET',
                    CHECK(secret != 'CHECK_SECRET'));
                INSERT INTO customers VALUES (1, 'ROW_SECRET');
                CREATE VIEW private_view AS SELECT id FROM customers WHERE secret != 'VIEW_SECRET';
            """)
        settings = patch("src.ai.api_settings", return_value={"api_key": "test-only", "sql_model": "gpt-4o-mini"})
        settings.start()
        self.addCleanup(settings.stop)
        client = patch("src.ai.create_client")
        self.client_mock = client.start()
        self.addCleanup(client.stop)
        self.request = self.client_mock.return_value.__enter__.return_value.responses.create
        self.request.return_value = response()

    def test_prompt_excludes_rows_defaults_view_sql_paths_and_history(self) -> None:
        result = generate_sql("Ile jest klientów?", get_schema(self.db_path))
        arguments = self.request.call_args.kwargs
        payload = json.loads(arguments["input"])
        self.assertEqual(set(payload), {"question", "schema", "current_date"})
        self.assertIn("customers", arguments["input"])
        for sensitive in ("DEFAULT_SECRET", "CHECK_SECRET", "VIEW_SECRET", "ROW_SECRET", str(self.db_path)):
            self.assertNotIn(sensitive, arguments["input"])
        self.assertFalse(arguments["store"])
        self.assertNotIn("tools", arguments)
        self.assertEqual(arguments["max_output_tokens"], 1200)
        self.assertNotIn("reasoning", arguments)
        self.assertEqual(result["usage"]["estimated_usd"], 0.00048)

    def test_sql_input_stops_before_database_access_or_api(self) -> None:
        with patch("src.analysis.get_schema") as schema, patch("src.analysis.execute_query") as execute:
            entry = analyze_question("-- private\nSELECT 'PRIVATE_VALUE'", str(self.db_path))
        self.assertEqual(entry["source"], "blocked")
        self.assertEqual(entry["error_reason"], "sql_input")
        schema.assert_not_called()
        execute.assert_not_called()
        with self.assertRaises(AIError):
            generate_sql("```sql\nSELECT 'PRIVATE_VALUE'\n```", get_schema(self.db_path))
        self.client_mock.assert_not_called()

    def test_legacy_sql_question_cannot_leak_through_context(self) -> None:
        schema = get_schema(self.db_path)
        with self.assertRaises(AIError):
            generate_sql("A teraz sierpień?", schema, previous_analysis={
                "question": "SELECT 'PRIVATE_VALUE'", "sql": "SELECT COUNT(*) FROM customers",
            })
        with self.assertRaises(AIError):
            generate_sql("Cały okres", schema, context=[{
                "question": "SELECT 'PRIVATE_VALUE'", "clarification": "Jaki okres?",
            }])
        self.client_mock.assert_not_called()

    def test_luna_uses_bounded_reasoning_and_counts_billed_output_once(self) -> None:
        reply = response()
        reply.usage.output_tokens = 1200
        reply.usage.output_tokens_details = SimpleNamespace(reasoning_tokens=900)
        self.request.return_value = reply
        with patch("src.ai.api_settings", return_value={"api_key": "test-only", "sql_model": "gpt-6-luna"}):
            result = generate_sql("Ile jest klientów?", get_schema(self.db_path))
        arguments = self.request.call_args.kwargs
        self.assertEqual(arguments["model"], "gpt-6-luna")
        self.assertEqual(arguments["reasoning"], {"effort": "low"})
        self.assertEqual(arguments["max_output_tokens"], 4000)
        self.assertTrue(arguments["text"]["format"]["strict"])
        self.assertFalse(arguments["store"])
        self.assertEqual(result["usage"]["output_tokens"], 1200)
        self.assertAlmostEqual(result["usage"]["estimated_usd"], 0.0008)

    def test_unknown_model_has_usage_without_an_invented_price(self) -> None:
        with patch("src.ai.api_settings", return_value={"api_key": "test-only", "sql_model": "unlisted-test-model"}):
            result = generate_sql("Ile jest klientów?", get_schema(self.db_path))
        self.assertEqual(result["usage"]["input_tokens"], 2000)
        self.assertIsNone(result["usage"]["estimated_usd"])

    def test_question_executes_on_the_selected_database(self) -> None:
        result = analyze_question("How many customers?", str(self.db_path))
        self.assertIsNone(result["error"])
        self.assertEqual(result["frame"].iloc[0, 0], 1)
        self.assertEqual(result["question"], "How many customers?")
        self.assertEqual(self.request.call_count, 1)

    def test_one_repair_sends_only_generic_error_category(self) -> None:
        self.request.side_effect = [response("SELECT wrong_column FROM customers"), response()]
        result = analyze_question("Ile klientów?", str(self.db_path))
        self.assertTrue(result["repaired"])
        self.assertEqual(result["frame"].iloc[0, 0], 1)
        self.assertEqual(self.request.call_count, 2)
        payload = json.loads(self.request.call_args.kwargs["input"])
        self.assertEqual(payload["repair"]["error_kind"], "unknown_column")
        self.assertEqual(set(payload["repair"]), {"sql", "error_kind"})
        self.assertNotIn("no such column", self.request.call_args.kwargs["input"])
        self.assertNotIn("ROW_SECRET", self.request.call_args.kwargs["input"])

    def test_second_failure_does_not_trigger_a_third_request(self) -> None:
        self.request.side_effect = [response("SELECT missing FROM customers"), response("SELECT missing2 FROM customers")]
        result = analyze_question("Ile klientów?", str(self.db_path))
        self.assertIsNotNone(result["error"])
        self.assertEqual(self.request.call_count, 2)

    def test_generated_or_repaired_write_is_never_executed(self) -> None:
        for replies, expected_calls in (([response("DROP TABLE customers")], 1),
                                        ([response("SELECT missing FROM customers"), response("DELETE FROM customers")], 2)):
            with self.subTest(expected_calls=expected_calls):
                self.request.reset_mock()
                self.request.side_effect = replies
                result = analyze_question("Usuń klientów", str(self.db_path))
                self.assertIsNotNone(result["error"])
                self.assertEqual(self.request.call_count, expected_calls)
                self.assertEqual(execute_query("SELECT COUNT(*) FROM customers", self.db_path)[0][0][0], 1)

    def test_timeout_is_not_repaired(self) -> None:
        with patch("src.analysis.execute_query", side_effect=sqlite3.OperationalError("interrupted")):
            result = analyze_question("Ile klientów?", str(self.db_path))
        self.assertIn("zbyt długo", result["error"])
        self.assertEqual(self.request.call_count, 1)

    def test_clarification_does_not_execute_sql(self) -> None:
        self.request.return_value = response(None, "Za jaki okres?")
        with patch("src.analysis.execute_query") as run:
            result = analyze_question("Ile było?", str(self.db_path))
        run.assert_not_called()
        self.assertEqual(result["clarification"], "Za jaki okres?")

    def test_clarification_payload_contains_only_the_two_allowed_text_fields(self) -> None:
        context = [{"question": "Ile klientów?", "clarification": "Z jakiego miasta?",
                    "sql": "SQL_SECRET", "frame": "ROW_SECRET", "path": str(self.db_path)}]
        generate_sql("Wrocław", get_schema(self.db_path), context=context)
        arguments = self.request.call_args.kwargs
        payload = json.loads(arguments["input"])
        self.assertEqual(payload["clarification_context"], [
            {"question": "Ile klientów?", "clarification": "Z jakiego miasta?"}
        ])
        for secret in ("SQL_SECRET", "ROW_SECRET", "DEFAULT_SECRET", str(self.db_path)):
            self.assertNotIn(secret, arguments["input"])
        self.assertFalse(arguments["store"])
        self.assertNotIn("previous_response_id", arguments)
        self.assertNotIn("conversation", arguments)

    def test_repair_keeps_clarification_context_without_sending_result_values(self) -> None:
        context = [{"question": "Ile klientów?", "clarification": "Z jakiego miasta?"}]
        self.request.side_effect = [response("SELECT missing FROM customers"), response()]
        result = analyze_question("Wrocław", str(self.db_path), context=context)
        self.assertTrue(result["repaired"])
        self.assertEqual(result["frame"].iloc[0, 0], 1)
        for call in self.request.call_args_list:
            payload = json.loads(call.kwargs["input"])
            self.assertEqual(payload["question"], "Wrocław")
            self.assertEqual(payload["clarification_context"], context)
            self.assertNotIn("ROW_SECRET", call.kwargs["input"])
        self.assertEqual(get_clarification_context([result]), [])

    def test_failed_followup_can_retry_with_the_original_clarification(self) -> None:
        http_response = SimpleNamespace(status_code=401, request=None, headers={})
        self.request.side_effect = [response(None, "Za jaki okres?"),
                                    AuthenticationError("SECRET", response=http_response, body=None), response()]
        first = analyze_question("Ile klientów?", str(self.db_path))
        context = get_clarification_context([first])
        failed = analyze_question("Cały okres", str(self.db_path), context=context)
        self.assertIsNotNone(failed["error"])
        recovered_context = get_clarification_context([first, failed])
        self.assertEqual(recovered_context, context)
        result = analyze_question("Cały okres", str(self.db_path), context=recovered_context)
        self.assertIsNone(result["error"])
        self.assertEqual(result["frame"].iloc[0, 0], 1)

    def test_clarification_limits_block_requests_before_api(self) -> None:
        for context in ([{"question": "Ile?", "clarification": "Kiedy?"}] * 6,
                        [{"question": "x" * 12000, "clarification": "Kiedy?"}]):
            with self.subTest(context_size=len(context)), self.assertRaises(AIError):
                generate_sql("Lipiec", get_schema(self.db_path), context=context)
        self.request.assert_not_called()

    def test_followup_sends_only_previous_question_and_sql_without_result_values(self) -> None:
        first = analyze_question("Ile mamy klientów?", str(self.db_path))
        first["frame"] = "ROW_SECRET"
        previous = get_previous_analysis([first])
        self.assertEqual(previous, {"question": "Ile mamy klientów?", "sql": first["sql"]})
        previous.update({"frame": "ROW_SECRET", "path": str(self.db_path), "usage": "PRIVATE_USAGE"})
        result = analyze_question("A tylko pierwszych pięciu?", str(self.db_path), previous_analysis=previous)
        payload = json.loads(self.request.call_args.kwargs["input"])
        self.assertEqual(payload["question"], "A tylko pierwszych pięciu?")
        self.assertEqual(set(payload["previous_analysis"]), {"question", "sql"})
        self.assertEqual(result["previous_analysis"], payload["previous_analysis"])
        for secret in ("ROW_SECRET", "DEFAULT_SECRET", "PRIVATE_USAGE", str(self.db_path)):
            self.assertNotIn(secret, self.request.call_args.kwargs["input"])

    def test_followup_clarification_and_repair_keep_the_same_completed_analysis(self) -> None:
        previous = {"question": "Ilu klientów?", "sql": "SELECT COUNT(*) FROM customers"}
        self.request.side_effect = [response(None, "Z jakiego miasta?"),
                                    response("SELECT missing FROM customers"), response()]
        clarification = analyze_question("A z jednego miasta?", str(self.db_path), previous_analysis=previous)
        self.assertEqual(get_previous_analysis([clarification]), previous)
        result = analyze_question("Wrocław", str(self.db_path), context=get_clarification_context([clarification]),
                                  previous_analysis=get_previous_analysis([clarification]))
        self.assertTrue(result["repaired"])
        for call in self.request.call_args_list:
            self.assertEqual(json.loads(call.kwargs["input"])["previous_analysis"], previous)
        self.assertEqual(get_previous_analysis([result]), {"question": "Wrocław", "sql": result["sql"]})

    def test_manual_sql_and_cancelled_entries_are_context_boundaries(self) -> None:
        completed = analyze_question("Ilu klientów?", str(self.db_path))
        for boundary in ({"sql": "SELECT 'MANUAL_SECRET'", "source": "sql"},
                         dict(completed, context_cancelled=True),
                         dict(completed, clarification_cancelled=True)):
            self.assertIsNone(get_previous_analysis([completed, boundary]))
            self.assertEqual(get_clarification_context([completed, boundary]), [])

    def test_previous_analysis_limits_block_requests_before_api(self) -> None:
        for previous in ({"question": "x" * 2001, "sql": "SELECT 1"},
                         {"question": "Ile?", "sql": "x" * 10001}, {"sql": "SELECT 1"}):
            with self.subTest(previous=previous), self.assertRaises(AIError):
                generate_sql("A teraz sierpień", get_schema(self.db_path), previous_analysis=previous)
        self.request.assert_not_called()

    def test_incomplete_responses_keep_usage_and_never_execute_partial_sql(self) -> None:
        for reason, category, text in (("max_output_tokens", "output_limit", "limit długości"),
                                       ("content_filter", "content_filter", "filtr treści"),
                                       ("SECRET_UNKNOWN_REASON", "incomplete", "nie ukończyło")):
            with self.subTest(reason=reason), patch("src.analysis.execute_query") as run:
                self.request.reset_mock()
                self.request.return_value = response(status="incomplete", reason=reason)
                result = analyze_question("Ilu klientów?", str(self.db_path))
                self.assertIn(text, result["error"])
                self.assertNotIn("SECRET", result["error"])
                self.assertEqual(result["error_reason"], category)
                self.assertEqual(result["api_usage"][0]["output_tokens"], 300)
                self.assertAlmostEqual(result["api_usage"][0]["estimated_usd"], 0.00048)
                self.assertEqual(self.request.call_count, 1)
                run.assert_not_called()

    def test_missing_usage_does_not_invent_zero_cost(self) -> None:
        for status in ("incomplete", "completed"):
            with self.subTest(status=status):
                reply = response(status=status)
                reply.usage = None
                self.request.return_value = reply
                result = analyze_question("Ilu klientów?", str(self.db_path))
                self.assertEqual(result["api_usage"], [])

    def test_failed_repair_keeps_usage_for_both_requests(self) -> None:
        self.request.side_effect = [response("SELECT missing FROM customers"),
                                    response(status="incomplete", reason="max_output_tokens")]
        result = analyze_question("Ilu klientów?", str(self.db_path))
        self.assertIn("limit długości", result["error"])
        self.assertEqual(len(result["api_usage"]), 2)
        self.assertEqual(self.request.call_count, 2)
        self.assertAlmostEqual(sum(item["estimated_usd"] for item in result["api_usage"]), 0.00096)

    def test_invalid_or_refused_responses_keep_usage_without_exposing_raw_text(self) -> None:
        invalid = response()
        invalid.output_text = "PRIVATE_RAW_TEXT"
        refused = response()
        refused.output = [SimpleNamespace(content=[SimpleNamespace(type="refusal", refusal="PRIVATE_RAW_TEXT")])]
        for reply, reason in ((invalid, "invalid_response"), (refused, "refusal")):
            with self.subTest(reason=reason):
                self.request.return_value = reply
                result = analyze_question("Ilu klientów?", str(self.db_path))
                self.assertEqual(result["error_reason"], reason)
                self.assertEqual(len(result["api_usage"]), 1)
                self.assertNotIn("PRIVATE_RAW_TEXT", result["error"])

    def test_malformed_incomplete_and_refused_responses_are_not_sql(self) -> None:
        replies = [response(None, None), response("SELECT 1", "Za jaki okres?"), response()]
        replies[-1].output_text = "not JSON"
        incomplete = response()
        incomplete.status = "incomplete"
        refused = response()
        refused.output = [SimpleNamespace(content=[SimpleNamespace(type="refusal")])]
        for reply in [*replies, incomplete, refused]:
            with self.subTest(reply=reply):
                self.request.return_value = reply
                with self.assertRaises(AIError):
                    generate_sql("Ile?", get_schema(self.db_path))

    def test_input_limits_do_not_make_api_requests(self) -> None:
        for question, schema in (("x" * 2001, "{}"), ("Ile?", "x" * 60001)):
            with self.assertRaises(AIError):
                generate_sql(question, schema)
        self.request.assert_not_called()

    def test_api_error_does_not_expose_provider_message(self) -> None:
        http_response = SimpleNamespace(status_code=401, request=None, headers={})
        self.request.side_effect = AuthenticationError("SECRET_KEY_AND_PAYLOAD", response=http_response, body=None)
        result = analyze_question("Ile klientów?", str(self.db_path))
        self.assertIn("Klucz API", result["error"])
        self.assertNotIn("SECRET", result["error"])

    def test_readonly_cte_and_quoted_literals_work(self) -> None:
        rows, _ = execute_query("WITH totals AS (SELECT COUNT(*) AS n FROM customers) SELECT n, 'DELETE; --' AS label FROM totals", self.db_path)
        self.assertEqual(tuple(rows[0]), (1, "DELETE; --"))
        with self.assertRaises(sqlite3.Error):
            execute_query('SELECT "load_extension"(\'private_extension\')', self.db_path)


class ConfigurationTests(unittest.TestCase):
    def test_luna_is_the_default_without_reading_local_settings(self) -> None:
        with patch("src.ai.dotenv_values", return_value={}), patch("src.ai.os.environ", {}):
            self.assertEqual(api_settings()["sql_model"], "gpt-6-luna")

    def test_appended_model_setting_overrides_previous_value(self) -> None:
        # Synthetic settings only: never open the user's .env in tests.
        local = dotenv_values(stream=StringIO(
            "OPENAI_SQL_MODEL=gpt-4o-mini\n\nOPENAI_SQL_MODEL=gpt-6-luna\n"
        ))
        with patch("src.ai.dotenv_values", return_value=local), patch("src.ai.os.environ", {}):
            self.assertEqual(api_settings()["sql_model"], "gpt-6-luna")

    def test_missing_key_does_not_create_client(self) -> None:
        with patch("src.ai.api_settings", return_value={"api_key": "", "sql_model": "gpt-4o-mini"}), patch("src.ai.OpenAI") as client:
            self.assertFalse(api_configured())
            with self.assertRaises(AIError):
                create_client()
        client.assert_not_called()

    def test_client_uses_official_endpoint_timeout_and_no_retries(self) -> None:
        with patch("src.ai.api_settings", return_value={"api_key": "test-only"}), patch("src.ai.OpenAI") as client:
            create_client()
        self.assertEqual(client.call_args.kwargs["max_retries"], 0)
        self.assertEqual(client.call_args.kwargs["timeout"], 30.0)
        self.assertEqual(client.call_args.kwargs["base_url"], "https://api.openai.com/v1")


if __name__ == "__main__":
    unittest.main()
