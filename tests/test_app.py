"""Exercise conversations and result presentation through Streamlit."""

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.db import execute_query
from src.storage import add_analysis, list_imported_databases, register_database
from src.analysis import load_result, new_entry
from src.db import DEFAULT_DB_PATH
from tests.test_ai import response


APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


class AppTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        storage_patch = patch("src.storage.DEFAULT_STORAGE_PATH", Path(temporary.name) / "history.db")
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        settings_patch = patch("src.ai.api_settings", return_value={"api_key": "test-only", "sql_model": "gpt-4o-mini"})
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        client = patch("src.ai.create_client")
        self.request = client.start().return_value.__enter__.return_value.responses.create
        self.addCleanup(client.stop)

    def ask_result(self, app, sql: str, question: str = "Pokaż wynik analizy") -> None:
        """Exercise the real question flow with a controlled model response."""
        self.request.return_value = response(sql)
        app.chat_input(key="sql_input").set_value(question).run()

    def test_conversations_and_tables_do_not_rerun_sql(self) -> None:
        with patch("src.db.execute_query", wraps=execute_query) as run_sql:
            app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
            self.assertFalse(app.exception)
            self.assertEqual(run_sql.call_count, 0)
            self.assertFalse(app.session_state.conversations[0]["entries"])

            app.button(key="Najlepsze produkty").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(run_sql.call_count, 1)
            self.assertEqual(app.session_state.conversations[0]["title"], "Najlepsze produkty")

            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(run_sql.call_count, 1)
            self.assertFalse(app.tabs)
            self.assertFalse(app.get("plotly_chart"))

            app.button(key="new_chat").click().run()
            app.button(key="Przychód miesięczny").click().run()
            self.assertEqual(len(app.session_state.conversations), 2)
            self.assertEqual(len(app.dataframe[0].value), 6)
            app.button(key="conversation_1").click().run()
            self.assertEqual(len(app.dataframe[0].value), 8)
            self.assertEqual(app.dataframe[0].value.columns.tolist(), ["Produkt", "Sprzedane sztuki", "Przychód [zł]"])
            self.assertEqual(run_sql.call_count, 2)
            self.assertFalse(app.exception)

    def test_scalar_is_presented_in_a_table(self) -> None:
        app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        self.ask_result(app, "SELECT 2989.45 AS przychod_pln")
        self.assertFalse(app.exception)
        self.assertEqual(app.dataframe[0].value.columns.tolist(), ["Przychód [zł]"])
        self.assertEqual(app.dataframe[0].value.iloc[0, 0], 2989.45)
        self.assertFalse(app.metric)
        self.assertEqual(len(app.get("plotly_chart")), 0)

    def test_second_tab_uses_database_changed_in_an_empty_conversation(self) -> None:
        custom = self.directory / "custom.db"
        with sqlite3.connect(custom) as connection:
            connection.execute("CREATE TABLE readings (value INTEGER)")
            connection.execute("INSERT INTO readings VALUES (7)")
        register_database({"id": "custom", "name": "Pomiary", "path": str(custom)})
        first = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        second = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        first.selectbox(key="database_selection").set_value("custom").run()
        self.ask_result(second, "SELECT value FROM readings")
        self.assertFalse(second.exception)
        self.assertFalse(second.error)
        self.assertEqual(second.dataframe[0].value.iloc[0, 0], 7)
        self.assertEqual(second.session_state.conversations[0]["database_id"], "custom")

    def test_new_chat_refreshes_messages_added_after_the_other_tab_opened(self) -> None:
        first = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        second = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        self.ask_result(first, "SELECT 42 AS total")
        second.button(key="new_chat").click().run()
        self.assertFalse(second.exception)
        self.assertEqual(second.session_state.active_conversation, 2)
        self.assertEqual(second.session_state.conversations[0]["entries"][0]["frame"].iloc[0, 0], 42)
        self.assertFalse(second.session_state.conversations[1]["entries"])

    def test_error_keeps_previous_analysis_available(self) -> None:
        app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        app.button(key="Przychód miesięczny").click().run()
        app.chat_input(key="sql_input").set_value("DELETE FROM orders").run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.error), 1)
        self.assertEqual(len(app.session_state.conversations[0]["entries"]), 2)
        self.assertEqual(len(app.chat_message), 4)
        self.assertEqual(len(app.dataframe[0].value), 6)
        self.ask_result(app, "SELECT COUNT(*) AS liczba_zamowien FROM orders")
        self.assertFalse(app.exception)
        self.assertEqual(len(app.chat_message), 6)
        self.assertEqual(app.dataframe[-1].value.iloc[0, 0], 60)

    def test_empty_and_duplicate_column_results_are_displayable(self) -> None:
        app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        self.ask_result(app, "SELECT * FROM customers WHERE 1 = 0")
        self.assertFalse(app.exception)
        self.assertTrue(any("nie zwróciło danych" in item.value for item in app.info))
        self.ask_result(app, "SELECT 1 AS quantity, 2 AS quantity")
        self.assertFalse(app.exception)
        self.assertEqual(app.dataframe[0].value.columns.tolist(), ["Liczba sztuk", "Liczba sztuk (2)"])

    def test_all_messages_survive_a_new_session(self) -> None:
        app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        for number in range(22):
            self.ask_result(app, f"SELECT {number} AS wynik")
            self.assertFalse(app.exception)
        history = app.session_state.conversations[0]["entries"]
        self.assertEqual(len(history), 22)
        restored = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        self.assertFalse(restored.exception)
        self.assertEqual(len(restored.session_state.conversations[0]["entries"]), 22)
        self.assertEqual(restored.dataframe[-1].value.iloc[0, 0], 21)

    def test_new_conversation_reuses_empty_chat_and_saves_all_chats(self) -> None:
        app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        app.button(key="new_chat").click().run()
        self.assertEqual(len(app.session_state.conversations), 1)
        for number in range(22):
            self.ask_result(app, f"SELECT {number} AS wynik")
            app.button(key="new_chat").click().run()
            self.assertFalse(app.exception)
        self.assertEqual(len(app.session_state.conversations), 23)
        self.assertEqual(app.session_state.active_conversation, 23)
        restored = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        self.assertFalse(restored.exception)
        self.assertEqual(len(restored.session_state.conversations), 23)

    def test_result_table_survives_reload_without_executing_sql(self) -> None:
        with patch("src.db.execute_query", wraps=execute_query) as run_sql:
            app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
            app.button(key="Najlepsze produkty").click().run()
            restored = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
            self.assertFalse(restored.exception)
            self.assertEqual(len(restored.dataframe[0].value), 8)
            self.assertFalse(restored.tabs)
            self.assertFalse(restored.get("plotly_chart"))
            self.assertEqual(run_sql.call_count, 1)

    def test_data_browser_shows_rows_and_column_metadata(self) -> None:
        app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        app.button(key="open_data").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox(key="table_demo").options, ["customers", "order_items", "orders", "products"])
        data = next(item.value for item in app.dataframe if "customer_id" in item.value.columns)
        self.assertEqual(len(data), 20)
        metadata = next(item.value for item in app.dataframe if "Kolumna" in item.value.columns)
        self.assertIn("email", metadata["Kolumna"].tolist())

    def test_toolbar_has_no_sql_editor(self) -> None:
        app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        self.assertNotIn("open_sql", [button.key for button in app.button])
        self.assertNotIn("SQL", app.chat_input(key="sql_input").placeholder)
        self.assertFalse(app.get("file_uploader"))
        self.assertFalse(app.text_area)

    def test_database_selection_keeps_conversations_and_results_separate(self) -> None:
        custom = self.directory / "custom.db"
        with sqlite3.connect(custom) as connection:
            connection.execute("CREATE TABLE readings (value INTEGER)")
            connection.executemany("INSERT INTO readings VALUES (?)", [(3,), (9,)])
        register_database({"id": "custom", "name": "Pomiary", "path": str(custom)})
        app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        app.button(key="Przychód miesięczny").click().run()
        app.selectbox(key="database_selection").set_value("custom").run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state.active_conversation, 2)
        self.assertNotIn("Przychód miesięczny", [button.key for button in app.button])
        self.ask_result(app, "SELECT SUM(value) AS suma FROM readings")
        self.assertEqual(app.dataframe[0].value.iloc[0, 0], 12)
        app.button(key="conversation_1").click().run()
        self.assertEqual(app.selectbox(key="database_selection").value, "demo")
        self.assertEqual(len(app.dataframe[0].value), 6)
        restored = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        self.assertFalse(restored.exception)
        self.assertEqual(restored.selectbox(key="database_selection").value, "demo")
        self.assertEqual(restored.session_state.active_conversation, 1)
        self.assertEqual(len(restored.dataframe[0].value), 6)

    def test_rename_and_permanent_delete_conversation(self) -> None:
        app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        app.button(key="Najlepsze produkty").click().run()
        app.text_input(key="conversation_name_1").set_value("Ranking produktów")
        app.button(key="save_chat_name_1").click().run()
        self.assertEqual(app.button(key="conversation_1").label, "Ranking produktów")
        self.assertNotIn("Szukaj rozmów", [item.label for item in app.text_input])
        self.assertNotIn("open_chat_trash", [item.key for item in app.button])
        renamed = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        self.assertEqual(renamed.button(key="conversation_1").label, "Ranking produktów")
        app.button(key="remove_chat_1").click().run()
        self.assertFalse(app.exception)
        self.assertNotEqual(app.session_state.active_conversation, 1)
        self.assertEqual([item["id"] for item in app.session_state.conversations], [2])
        self.assertFalse(app.session_state.conversations[0]["entries"])
        reloaded = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        self.assertFalse(reloaded.exception)
        self.assertEqual(reloaded.session_state.active_conversation, 2)
        self.assertNotIn("conversation_1", [item.key for item in reloaded.button])
        self.assertEqual(len(reloaded.get("plotly_chart")), 0)

    def test_delete_inactive_conversation_keeps_the_open_result(self) -> None:
        with patch("src.db.execute_query", wraps=execute_query) as run_sql:
            app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
            app.button(key="Najlepsze produkty").click().run()
            app.button(key="new_chat").click().run()
            app.button(key="Przychód miesięczny").click().run()
            app.button(key="conversation_1").click().run()
            app.button(key="remove_chat_2").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state.active_conversation, 1)
            self.assertEqual(len(app.dataframe[0].value), 8)
            self.assertEqual(run_sql.call_count, 2)
            reloaded = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
            self.assertEqual(reloaded.session_state.active_conversation, 1)
            self.assertNotIn("conversation_2", [item.key for item in reloaded.button])

    def test_other_tab_handles_a_deleted_conversation_without_restoring_it(self) -> None:
        first = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        self.ask_result(first, "SELECT 42 AS total")
        other = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        first.button(key="remove_chat_1").click().run()
        other.button(key="open_data").click().run()
        self.assertFalse(other.exception)
        self.assertEqual(other.session_state.active_conversation, first.session_state.active_conversation)
        self.assertNotIn("conversation_1", [item.key for item in other.button])
        self.assertFalse(other.text_area)
        self.assertEqual(len(other.get("plotly_chart")), 0)

    def test_legacy_manual_result_remains_readable_without_editing_or_execution(self) -> None:
        app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        entry = new_entry("Własna analiza SQL", "SELECT 2 AS wynik")
        load_result(entry, str(DEFAULT_DB_PATH))
        add_analysis(1, entry)
        with patch("src.db.execute_query") as run_sql:
            app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.dataframe[0].value.iloc[0, 0], 2)
            self.assertTrue(any(code.value == "SELECT 2 AS wynik" for code in app.code))
            self.assertFalse(any((button.key or "").startswith("edit_sql_") for button in app.button))
            run_sql.assert_not_called()
            self.request.assert_not_called()

    def test_database_manager_renames_removes_and_restores_without_losing_results(self) -> None:
        custom = self.directory / "custom.db"
        with sqlite3.connect(custom) as connection:
            connection.execute("CREATE TABLE values_demo (amount INTEGER)")
            connection.execute("INSERT INTO values_demo VALUES (7)")
        register_database({"id": "custom", "name": "custom.db", "path": str(custom)})
        original = custom.read_bytes()
        app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        app.selectbox(key="database_selection").set_value("custom").run()
        self.ask_result(app, "SELECT amount FROM values_demo")
        app.button(key="open_upload").click().run()
        app.text_input(key="database_name_custom").set_value("Pomiary")
        app.button(key="save_database_name_custom").click().run()
        self.assertEqual(list_imported_databases()[0]["name"], "Pomiary")
        app.button(key="remove_database_custom").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox(key="database_selection").value, "demo")
        self.assertEqual(list_imported_databases(), [])
        app.session_state.active_dialog = None
        app.run()
        app.button(key="conversation_1").click().run()
        self.assertEqual(app.dataframe[0].value.iloc[0, 0], 7)
        self.assertTrue(app.chat_input(key="sql_input").disabled)
        app.button(key="new_chat").click().run()
        self.assertEqual(app.selectbox(key="database_selection").value, "demo")
        app.button(key="open_upload").click().run()
        app.button(key="restore_database_custom").click().run()
        app.button(key="conversation_1").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox(key="database_selection").value, "custom")
        self.assertEqual(app.dataframe[0].value.iloc[0, 0], 7)
        self.assertEqual(custom.read_bytes(), original)

    def test_large_nullable_id_remains_exact_in_ui_after_reload(self) -> None:
        app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        self.ask_result(app, "SELECT 9223372036854775807 AS id UNION ALL SELECT NULL")
        self.assertFalse(app.exception)
        self.assertEqual(app.dataframe[0].value.iloc[0, 0], "9223372036854775807")
        restored = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        self.assertFalse(restored.exception)
        self.assertEqual(restored.dataframe[0].value.iloc[0, 0], "9223372036854775807")

    def test_corrupt_history_entry_does_not_break_chat(self) -> None:
        from src.storage import DEFAULT_STORAGE_PATH
        app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        self.ask_result(app, "SELECT 42 AS value")
        chat_id = app.session_state.active_conversation
        with sqlite3.connect(DEFAULT_STORAGE_PATH) as connection:
            connection.execute("INSERT INTO analyses (conversation_id, payload) VALUES (?, '{}')", (chat_id,))
        restored = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        self.assertFalse(restored.exception)
        self.assertTrue(any("Oryginalny zapis" in item.value for item in restored.warning))
        self.assertEqual(restored.dataframe[0].value.iloc[0, 0], 42)
        self.ask_result(restored, "SELECT 7 AS value", "Pokaż kolejny wynik")
        self.assertFalse(restored.exception)
        self.assertEqual(restored.dataframe[-1].value.iloc[0, 0], 7)

    def test_result_limit_is_local_error_and_next_question_still_works(self) -> None:
        app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        self.ask_result(app, "SELECT zeroblob(40000)")
        self.assertFalse(app.exception)
        self.assertTrue(any("64 KB" in item.value for item in app.error))
        self.assertEqual(self.request.call_count, 1)
        self.ask_result(app, "SELECT 7 AS value", "Pokaż mały wynik")
        self.assertFalse(app.exception)
        self.assertEqual(app.dataframe[-1].value.iloc[0, 0], 7)

    def test_money_table_and_export_controls_use_consistent_units(self) -> None:
        from decimal import Decimal
        import streamlit as st
        app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        self.ask_result(app, "SELECT 24990 AS price_cents")
        self.assertFalse(app.exception)
        self.assertEqual(app.dataframe[0].value.iloc[0, 0], Decimal("249.9"))
        self.assertTrue(st.get_option("client.disableDataExport"))
        labels = [button.label for button in app.get("download_button")]
        self.assertIn("Pobierz CSV do arkusza", labels)
        self.assertIn("Pobierz surowy CSV", labels)



if __name__ == "__main__":
    unittest.main()
