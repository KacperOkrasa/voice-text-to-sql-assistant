"""Exercise text questions and history without any network requests."""

import unittest
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.ai import create_client
from src.storage import add_analysis, load_conversations, register_database, save_analysis
from src.analysis import new_entry
from tests.test_ai import response

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


class AIAppTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        storage = patch("src.storage.DEFAULT_STORAGE_PATH", Path(temporary.name) / "history.db")
        storage.start()
        self.addCleanup(storage.stop)
        settings = patch("src.ai.api_settings", return_value={"api_key": "test-only", "sql_model": "gpt-4o-mini"})
        settings.start()
        self.addCleanup(settings.stop)
        client = patch("src.ai.create_client")
        self.request = client.start().return_value.__enter__.return_value.responses.create
        self.addCleanup(client.stop)
        self.request.return_value = response("SELECT COUNT(*) AS liczba_zamowien FROM orders")

    def app(self):
        return AppTest.from_file(str(APP_PATH), default_timeout=20).run()

    def test_question_result_and_usage_survive_reload_without_new_api_call(self) -> None:
        app = self.app()
        app.chat_input(key="sql_input").set_value("Ile jest zamówień?").run()
        self.assertFalse(app.exception)
        self.assertEqual(app.dataframe[0].value.iloc[0, 0], 60)
        self.assertEqual(load_conversations()[0]["entries"][0]["question"], "Ile jest zamówień?")
        app.run()
        restored = self.app()
        self.assertFalse(restored.exception)
        self.assertEqual(restored.dataframe[0].value.iloc[0, 0], 60)
        self.assertEqual(self.request.call_count, 1)
        self.assertEqual(restored.session_state.conversations[0]["entries"][0]["api_usage"][0]["input_tokens"], 2000)

    def test_luna_model_and_cost_are_displayed_and_preserved(self) -> None:
        with patch("src.ai.api_settings", return_value={"api_key": "test-only", "sql_model": "gpt-6-luna"}):
            app = self.app()
            app.chat_input(key="sql_input").set_value("Ile jest zamówień?").run()
            restored = self.app()
        self.assertFalse(app.exception)
        self.assertFalse(restored.exception)
        self.assertTrue(any("gpt-6-luna" in item.value for item in restored.caption))
        self.assertTrue(any("0.000350 USD" in item.value for item in restored.caption))
        self.assertEqual(load_conversations()[0]["entries"][0]["api_usage"][0]["model"], "gpt-6-luna")
        self.assertEqual(self.request.call_count, 1)

    def test_second_tab_follows_the_latest_saved_analysis(self) -> None:
        first_sql = "SELECT COUNT(*) FROM orders"
        latest_sql = "SELECT COUNT(*) FROM customers"
        self.request.side_effect = [response(first_sql), response(latest_sql),
                                    response("SELECT COUNT(*) FROM customers WHERE city = 'Warszawa'")]
        first = self.app()
        first.chat_input(key="sql_input").set_value("Ile zamówień?").run()
        second = self.app()
        first.chat_input(key="sql_input").set_value("Ilu klientów?").run()

        # No browser reload is needed before sending the next message.
        second.chat_input(key="sql_input").set_value("A tylko z Warszawy?").run()
        self.assertFalse(second.exception)
        payload = json.loads(self.request.call_args.kwargs["input"])
        self.assertEqual(payload["previous_analysis"], {"question": "Ilu klientów?", "sql": latest_sql})
        self.assertEqual(len(second.session_state.conversations[0]["entries"]), 3)
        self.assertEqual(len(load_conversations()[0]["entries"]), 3)
        second.run()
        first.run()
        self.assertEqual(self.request.call_count, 3)
        self.assertEqual(len(first.session_state.conversations[0]["entries"]), 3)

    def test_second_tab_sees_clarification_cancelled_elsewhere(self) -> None:
        self.request.side_effect = [response(None, "Za jaki okres?"), response("SELECT COUNT(*) FROM customers")]
        first = self.app()
        first.chat_input(key="sql_input").set_value("Ile zamówień?").run()
        second = self.app()
        first.button(key="clear_context").click().run()
        second.chat_input(key="sql_input").set_value("Ilu klientów?").run()
        self.assertFalse(second.exception)
        payload = json.loads(self.request.call_args.kwargs["input"])
        self.assertNotIn("clarification_context", payload)
        self.assertEqual(self.request.call_count, 2)

    def test_sql_is_rejected_and_presets_never_use_api_even_with_a_key(self) -> None:
        app = self.app()
        app.chat_input(key="sql_input").set_value("SELECT 42 AS total").run()
        self.assertIn("nie są obsługiwane", app.error[0].value)
        self.assertFalse(app.dataframe)
        self.assertNotIn("retry_1", [button.key for button in app.button])
        app.button(key="Najlepsze produkty").click().run()
        self.assertFalse(app.exception)
        self.request.assert_not_called()

    def test_clarification_is_saved_without_an_empty_result_table(self) -> None:
        self.request.return_value = response(None, "Za jaki okres?")
        app = self.app()
        app.chat_input(key="sql_input").set_value("Jaki przychód?").run()
        self.assertFalse(app.exception)
        self.assertFalse(app.dataframe)
        self.assertTrue(any(item.value == "Za jaki okres?" for item in app.markdown))
        self.assertEqual(load_conversations()[0]["entries"][0]["clarification"], "Za jaki okres?")

    def test_commented_sql_is_blocked_but_english_with_question_uses_ai(self) -> None:
        app = self.app()
        app.chat_input(key="sql_input").set_value("-- LOCAL_SECRET\nSELECT 'PRIVATE_VALUE'").run()
        self.assertFalse(app.exception)
        self.assertFalse(app.dataframe)
        self.assertIn("nie są obsługiwane", app.error[0].value)
        self.request.assert_not_called()
        question = "With cancelled orders included, how many orders are there?"
        app.chat_input(key="sql_input").set_value(question).run()
        self.assertEqual(app.dataframe[-1].value.iloc[0, 0], 60)
        self.assertEqual(self.request.call_count, 1)
        payload = self.request.call_args.kwargs["input"]
        self.assertNotIn("PRIVATE_VALUE", payload)
        self.assertNotIn("LOCAL_SECRET", payload)
        self.assertEqual(json.loads(payload)["question"], question)

    def test_retry_of_legacy_sql_message_is_blocked_before_api(self) -> None:
        app = self.app()
        entry = new_entry("Stary wpis")
        entry.update(question="SELECT 'PRIVATE_VALUE'", source="text", error="Błąd starej wersji")
        add_analysis(1, entry)
        app.run()
        app.button(key="retry_1").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(load_conversations()[0]["entries"][-1]["source"], "blocked")
        self.request.assert_not_called()

    def test_queued_editor_request_from_old_version_is_discarded(self) -> None:
        app = self.app()
        app.session_state.pending_request = ("SELECT 'PRIVATE_VALUE'", "Własna analiza", "demo")
        app.session_state.active_dialog = "sql"
        app.run()
        self.assertFalse(app.exception)
        self.assertIsNone(app.session_state.active_dialog)
        self.assertFalse(load_conversations()[0]["entries"])
        self.assertFalse(app.dataframe)
        self.request.assert_not_called()

    def test_missing_key_preserves_the_question(self) -> None:
        with patch("src.ai.api_settings", return_value={"api_key": "", "sql_model": "gpt-4o-mini"}), \
                patch("src.ai.create_client", wraps=create_client):
            app = self.app()
            app.chat_input(key="sql_input").set_value("Ile jest zamówień?").run()
        self.assertFalse(app.exception)
        self.assertIn("OPENAI_API_KEY", app.error[0].value)
        self.assertEqual(load_conversations()[0]["entries"][0]["question"], "Ile jest zamówień?")
        self.request.assert_not_called()

    def test_failed_history_save_keeps_paid_result_without_rerunning(self) -> None:
        app = self.app()
        with patch("src.storage.add_analysis", side_effect=OSError("disk full")):
            app.chat_input(key="sql_input").set_value("Ile jest zamówień?").run()
        self.assertFalse(app.exception)
        self.assertEqual(app.dataframe[0].value.iloc[0, 0], 60)
        self.assertTrue(app.session_state.conversations[0]["entries"][0]["unsaved"])
        app.run()
        self.assertEqual(app.dataframe[0].value.iloc[0, 0], 60)
        self.assertTrue(app.session_state.conversations[0]["entries"][0]["unsaved"])
        self.assertEqual(self.request.call_count, 1)

    def test_failed_context_save_stays_cancelled_in_the_current_tab(self) -> None:
        app = self.app()
        app.chat_input(key="sql_input").set_value("Ile zamówień?").run()
        with patch("src.storage.save_analysis", side_effect=OSError("disk full")):
            app.button(key="clear_context").click().run()
        app.run()
        app.chat_input(key="sql_input").set_value("Ilu klientów?").run()
        self.assertFalse(app.exception)
        self.assertNotIn("previous_analysis", json.loads(self.request.call_args.kwargs["input"]))
        self.assertEqual(self.request.call_count, 2)

    def test_short_clarification_reply_survives_reload_and_finishes_analysis(self) -> None:
        self.request.side_effect = [response(None, "Za jaki okres?"), response("SELECT COUNT(*) FROM orders"),
                                    response("SELECT COUNT(*) FROM customers")]
        app = self.app()
        app.chat_input(key="sql_input").set_value("Ile było zamówień?").run()
        restored = self.app()
        self.assertEqual(self.request.call_count, 1)
        restored.chat_input(key="sql_input").set_value("Cały okres").run()
        self.assertFalse(restored.exception)
        self.assertEqual(restored.dataframe[0].value.iloc[0, 0], 60)
        payload = json.loads(self.request.call_args.kwargs["input"])
        self.assertEqual(payload["question"], "Cały okres")
        self.assertEqual(payload["clarification_context"], [
            {"question": "Ile było zamówień?", "clarification": "Za jaki okres?"}
        ])
        history = load_conversations()[0]["entries"]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[1]["question"], "Cały okres")
        self.assertIn("clear_context", [button.key for button in restored.button])
        restored.chat_input(key="sql_input").set_value("Ilu mamy klientów?").run()
        payload = json.loads(self.request.call_args.kwargs["input"])
        self.assertNotIn("clarification_context", payload)
        self.assertEqual(payload["previous_analysis"]["sql"], "SELECT COUNT(*) FROM orders")

    def test_multiple_clarifications_keep_each_answer_in_order(self) -> None:
        self.request.side_effect = [response(None, "Za jaki okres?"), response(None, "Który status zamówień?"),
                                    response("SELECT COUNT(*) FROM orders WHERE status = 'delivered'")]
        app = self.app()
        app.chat_input(key="sql_input").set_value("Ile zamówień?").run()
        app.chat_input(key="sql_input").set_value("Cały okres").run()
        app = self.app()
        app.chat_input(key="sql_input").set_value("Dostarczone").run()
        self.assertFalse(app.exception)
        payload = json.loads(self.request.call_args.kwargs["input"])
        self.assertEqual(payload["clarification_context"], [
            {"question": "Ile zamówień?", "clarification": "Za jaki okres?"},
            {"question": "Cały okres", "clarification": "Który status zamówień?"},
        ])
        self.assertEqual(payload["question"], "Dostarczone")
        self.assertEqual(self.request.call_count, 3)

    def test_cancelled_clarification_stays_cancelled_after_reload(self) -> None:
        self.request.side_effect = [response(None, "Za jaki okres?"), response("SELECT COUNT(*) FROM customers")]
        app = self.app()
        app.chat_input(key="sql_input").set_value("Ile zamówień?").run()
        app.button(key="clear_context").click().run()
        app = self.app()
        app.chat_input(key="sql_input").set_value("Ilu klientów?").run()
        self.assertFalse(app.exception)
        self.assertNotIn("clarification_context", json.loads(self.request.call_args.kwargs["input"]))
        self.assertTrue(load_conversations()[0]["entries"][0]["context_cancelled"])

    def test_new_conversation_does_not_inherit_clarification(self) -> None:
        self.request.side_effect = [response(None, "Za jaki okres?"), response("SELECT COUNT(*) FROM customers")]
        app = self.app()
        app.chat_input(key="sql_input").set_value("Ile zamówień?").run()
        app.button(key="new_chat").click().run()
        app.chat_input(key="sql_input").set_value("Ilu klientów?").run()
        self.assertFalse(app.exception)
        self.assertNotIn("clarification_context", json.loads(self.request.call_args.kwargs["input"]))
        app.button(key="conversation_1").click().run()
        self.assertIn("clear_context", [button.key for button in app.button])

    def test_completed_followup_survives_reload_and_uses_the_latest_sql(self) -> None:
        first_sql = "SELECT customer_id FROM customers ORDER BY customer_id LIMIT 5"
        next_sql = "SELECT customer_id FROM customers ORDER BY customer_id LIMIT 10"
        self.request.side_effect = [response(first_sql), response(next_sql), response(next_sql + " OFFSET 10")]
        app = self.app()
        app.chat_input(key="sql_input").set_value("Pokaż pierwszych pięciu klientów").run()
        app = self.app()
        app.chat_input(key="sql_input").set_value("A teraz 10 zamiast 5").run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.dataframe[-1].value), 10)
        payload = json.loads(self.request.call_args.kwargs["input"])
        self.assertEqual(payload["previous_analysis"], {
            "question": "Pokaż pierwszych pięciu klientów", "sql": first_sql,
        })
        app = self.app()
        app.chat_input(key="sql_input").set_value("I kolejnych 10").run()
        self.assertFalse(app.exception)
        payload = json.loads(self.request.call_args.kwargs["input"])
        self.assertEqual(payload["previous_analysis"], {"question": "A teraz 10 zamiast 5", "sql": next_sql})
        self.assertEqual(self.request.call_count, 3)

    def test_reset_completed_context_is_persistent_and_does_not_call_api(self) -> None:
        app = self.app()
        app.chat_input(key="sql_input").set_value("Ile zamówień?").run()
        app.button(key="clear_context").click().run()
        app = self.app()
        self.assertEqual(self.request.call_count, 1)
        self.assertEqual(app.dataframe[0].value.iloc[0, 0], 60)
        self.assertNotIn("clear_context", [button.key for button in app.button])
        app.chat_input(key="sql_input").set_value("Ile produktów?").run()
        self.assertNotIn("previous_analysis", json.loads(self.request.call_args.kwargs["input"]))

    def test_completed_context_is_separate_for_new_chats_and_databases(self) -> None:
        custom = self.directory / "custom.db"
        with sqlite3.connect(custom) as connection:
            connection.execute("CREATE TABLE readings (value INTEGER)")
            connection.execute("INSERT INTO readings VALUES (7)")
        register_database({"id": "custom", "name": "Pomiary", "path": str(custom)})
        self.request.side_effect = [response("SELECT COUNT(*) FROM orders"),
                                    response("SELECT COUNT(*) FROM customers"), response("SELECT value FROM readings")]
        app = self.app()
        app.chat_input(key="sql_input").set_value("Ile zamówień?").run()
        app.button(key="new_chat").click().run()
        app.chat_input(key="sql_input").set_value("Ile klientów?").run()
        self.assertNotIn("previous_analysis", json.loads(self.request.call_args.kwargs["input"]))
        app.selectbox(key="database_selection").set_value("custom").run()
        app.chat_input(key="sql_input").set_value("Pokaż pomiary").run()
        self.assertFalse(app.exception)
        self.assertEqual(app.dataframe[0].value.iloc[0, 0], 7)
        payload = json.loads(self.request.call_args.kwargs["input"])
        self.assertNotIn("previous_analysis", payload)
        self.assertNotIn("orders", self.request.call_args.kwargs["input"])
        app.button(key="conversation_1").click().run()
        self.assertEqual(app.dataframe[0].value.iloc[0, 0], 60)
        self.assertEqual(self.request.call_count, 3)

    def test_followup_context_does_not_include_manually_entered_sql(self) -> None:
        app = self.app()
        app.chat_input(key="sql_input").set_value("Ile zamówień?").run()
        app.chat_input(key="sql_input").set_value("SELECT 'MANUAL_SECRET' AS label").run()
        app.chat_input(key="sql_input").set_value("Ile produktów?").run()
        payload = json.loads(self.request.call_args.kwargs["input"])
        self.assertNotIn("previous_analysis", payload)
        self.assertNotIn("MANUAL_SECRET", self.request.call_args.kwargs["input"])
        self.assertEqual(self.request.call_count, 2)

    def test_retry_keeps_original_question_and_usage_without_automatic_requests(self) -> None:
        self.request.side_effect = [response(status="incomplete", reason="max_output_tokens"), response()]
        app = self.app()
        app.chat_input(key="sql_input").set_value("Ile klientów?").run()
        self.assertIn("limit długości", app.error[0].value)
        app.run()
        app = self.app()
        self.assertEqual(self.request.call_count, 1)
        self.assertTrue(any("2300 tokenów" in item.value for item in app.caption))
        app.button(key="retry_1").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(self.request.call_count, 2)
        history = load_conversations()[0]["entries"]
        self.assertEqual(len(history), 2)
        self.assertIn("limit długości", history[0]["error"])
        self.assertEqual(history[0]["api_usage"][0]["output_tokens"], 300)
        self.assertIsNone(history[1]["error"])
        self.assertEqual(history[1]["retry_of"], 1)
        self.assertEqual(history[1]["question"], "Ile klientów?")
        app.run()
        self.app()
        self.assertEqual(self.request.call_count, 2)

    def test_retry_of_older_failure_restores_its_context_after_another_result(self) -> None:
        first_sql = "SELECT customer_id FROM customers LIMIT 5"
        self.request.side_effect = [response(first_sql), response(None, "Z jakiego miasta?"),
                                    response(status="incomplete", reason="max_output_tokens"),
                                    response("SELECT COUNT(*) FROM orders"), response("SELECT customer_id FROM customers")]
        app = self.app()
        app.chat_input(key="sql_input").set_value("Pokaż pięciu klientów").run()
        app.chat_input(key="sql_input").set_value("A tylko z jednego miasta?").run()
        app.chat_input(key="sql_input").set_value("Wrocław").run()
        failed = load_conversations()[0]["entries"][-1]
        app.button(key="clear_context").click().run()
        app.chat_input(key="sql_input").set_value("Ile zamówień?").run()
        app = self.app()
        app.button(key="retry_3").click().run()
        self.assertFalse(app.exception)
        payload = json.loads(self.request.call_args.kwargs["input"])
        self.assertEqual(payload["question"], "Wrocław")
        self.assertEqual(payload["clarification_context"], failed["clarification_context"])
        self.assertEqual(payload["previous_analysis"], failed["previous_analysis"])
        self.assertEqual(payload["previous_analysis"]["sql"], first_sql)
        self.assertEqual(self.request.call_count, 5)

    def test_failed_retry_remains_manual_and_preserves_both_usage_records(self) -> None:
        self.request.return_value = response(status="incomplete", reason="max_output_tokens")
        app = self.app()
        app.chat_input(key="sql_input").set_value("Ile zamówień?").run()
        app.button(key="retry_1").click().run()
        self.assertFalse(app.exception)
        app = self.app()
        self.assertEqual(self.request.call_count, 2)
        history = load_conversations()[0]["entries"]
        self.assertEqual(len(history), 2)
        self.assertTrue(all(entry["error"] and len(entry["api_usage"]) == 1 for entry in history))
        self.assertIn("retry_2", [button.key for button in app.button])

    def test_question_appears_once_with_a_short_result_heading(self) -> None:
        question = "Pokaż wszystkie zamówienia i ich przychód według miesięcy, " + "uwzględniając tylko dostarczone zamówienia. " * 3 + "<test>"
        app = self.app()
        app.chat_input(key="sql_input").set_value(question).run()
        old_entry = load_conversations()[0]["entries"][0]
        old_entry["title"] = question[:80]
        save_analysis(old_entry)
        restored = self.app()
        self.assertFalse(restored.exception)
        titles = [item.value for item in restored.markdown if 'class="result-title"' in item.value]
        self.assertEqual(titles, ['<div class="result-title">Wyniki</div>'])
        self.assertEqual(sum(item.value == question for item in restored.markdown), 1)


if __name__ == "__main__":
    unittest.main()
