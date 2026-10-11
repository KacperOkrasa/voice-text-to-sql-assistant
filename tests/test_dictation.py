"""Check live drafts without a microphone, real API settings or network calls."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from streamlit.components.v2.bidi_component.state import ComponentResult
from streamlit.testing.v1 import AppTest

from src.dictation import render_dictation, validate_dictation_update
from src.storage import load_conversations
from tests.test_ai import response

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


def update(target, text="Pokaż przychód", sequence=1, state="listening", error=None):
    return {"target": target, "session": "test-session", "sequence": sequence,
            "text": text, "state": state, "error": error}


class DictationValidationTests(unittest.TestCase):
    def test_component_result_update_field_is_not_confused_with_the_dict_method(self):
        event = update("chat")
        # Use Streamlit's real return type, including its inherited dict.update.
        component = Mock(return_value=ComponentResult(state_vals={"update": event}))
        with patch("src.dictation.dictation_component", return_value=component):
            result = render_dictation("chat")
        self.assertIsInstance(result, dict)
        self.assertEqual(validate_dictation_update(result, "chat")["text"], "Pokaż przychód")

    def test_repeated_older_and_other_conversation_events_are_ignored(self):
        self.assertIsNone(validate_dictation_update(update("old-chat"), "new-chat"))
        self.assertIsNone(validate_dictation_update(update("chat", sequence=1), "chat", ("test-session", 2)))
        self.assertIsNotNone(validate_dictation_update(update("chat", sequence=3), "chat", ("test-session", 2)))

    def test_malformed_events_and_raw_error_messages_never_reach_ui(self):
        for event in (None, "SECRET", update("chat", text="a" * 2001),
                      update("chat", state=[]),
                      update("chat", sequence=True), update("chat", error={"secret": "key"})):
            self.assertIsNone(validate_dictation_update(event, "chat"))
        result = validate_dictation_update(update("chat", error="SECRET_PROVIDER_PAYLOAD"), "chat")
        self.assertNotIn("SECRET", result["error"])


class LiveDictationAppTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        storage = patch("src.storage.DEFAULT_STORAGE_PATH", Path(temporary.name) / "history.db")
        storage.start()
        self.addCleanup(storage.stop)
        settings = patch("src.ai.api_settings", return_value={"api_key": "test-only", "sql_model": "gpt-6-luna"})
        settings.start()
        self.addCleanup(settings.stop)
        client = patch("src.ai.create_client")
        self.client = client.start()
        self.addCleanup(client.stop)
        self.sql = self.client.return_value.__enter__.return_value.responses.create
        self.sql.return_value = response("SELECT COUNT(*) AS total FROM orders")
        component = patch("src.dictation.render_dictation", return_value=None)
        self.component = component.start()
        self.addCleanup(component.stop)

    def app(self):
        return AppTest.from_file(str(APP_PATH), default_timeout=20).run()

    def test_partial_corrections_prefill_text_without_submitting_or_saving(self):
        app = self.app()
        target = app.session_state["dictation_target"]
        self.component.return_value = update(target, "Pokaż przyhód")
        app.run()
        self.assertFalse(app.exception)
        self.assertTrue(app.chat_input[0].proto.set_value)
        self.assertEqual(app.chat_input[0].proto.value, "Pokaż przyhód")
        self.assertTrue(app.chat_input[0].disabled)
        self.component.return_value = update(target, "Pokaż przychód miesięczny", sequence=2)
        app.run()
        self.assertEqual(app.chat_input[0].proto.value, "Pokaż przychód miesięczny")
        self.client.assert_not_called()
        self.assertEqual(load_conversations()[0]["entries"], [])

    def test_stopping_then_editing_and_sending_uses_luna_once(self):
        app = self.app()
        target = app.session_state["dictation_target"]
        self.component.return_value = update(target, "Ile zamówień?", state="idle")
        app.run()
        self.assertFalse(app.chat_input[0].disabled)
        app.run()
        self.assertFalse(app.chat_input[0].proto.set_value)
        app.chat_input(key="sql_input").set_value("Ile jest wszystkich zamówień?").run()
        self.assertFalse(app.exception)
        self.assertEqual(self.sql.call_count, 1)
        payload = json.loads(self.sql.call_args.kwargs["input"])
        self.assertEqual(payload["question"], "Ile jest wszystkich zamówień?")
        self.assertEqual(self.sql.call_args.kwargs["model"], "gpt-6-luna")
        self.client.return_value.__enter__.return_value.audio.transcriptions.create.assert_not_called()
        entry = load_conversations()[0]["entries"][0]
        self.assertEqual(entry["input_method"], "voice")
        self.assertEqual(entry["dictation_provider"], "browser")
        self.assertNotIn("transcription_usage", entry)
        self.assertNotIn("audio", entry)

    def test_old_chat_events_cannot_prefill_a_new_chat(self):
        app = self.app()
        app.chat_input(key="sql_input").set_value("Ile zamówień?").run()
        old_target = app.session_state["dictation_target"]
        self.component.return_value = update(old_target, "Stare pytanie")
        app.run()
        app.button(key="new_chat").click().run()
        self.assertNotEqual(app.session_state["dictation_target"], old_target)
        self.assertNotEqual(app.chat_input[0].proto.value, "Stare pytanie")
        self.assertFalse(app.chat_input[0].disabled)
        self.assertEqual(self.sql.call_count, 1)

    def test_sql_pasted_after_dictation_is_never_sent_or_executed(self):
        app = self.app()
        target = app.session_state["dictation_target"]
        self.component.return_value = update(target, "Ile zamówień?", state="idle")
        app.run()
        with patch("src.analysis.execute_query") as execute:
            app.chat_input(key="sql_input").set_value("SELECT 'PRIVATE_TEST_LITERAL' AS value").run()
        self.assertFalse(app.exception)
        self.assertIn("nie są obsługiwane", app.error[0].value)
        self.assertFalse(app.dataframe)
        self.assertNotIn("retry_1", [button.key for button in app.button])
        execute.assert_not_called()
        self.client.assert_not_called()
        app.chat_input(key="sql_input").set_value("Ile zamówień?").run()
        self.assertEqual(self.sql.call_count, 1)
        self.assertNotIn("PRIVATE_TEST_LITERAL", self.sql.call_args.kwargs["input"])

    def test_voice_followup_preserves_previous_analysis_context(self):
        app = self.app()
        app.chat_input(key="sql_input").set_value("Ile zamówień?").run()
        target = app.session_state["dictation_target"]
        self.component.return_value = update(target, "Tylko w sierpniu", state="idle")
        app.run()
        app.chat_input(key="sql_input").set_value("Tylko w sierpniu").run()
        payload = json.loads(self.sql.call_args.kwargs["input"])
        self.assertEqual(payload["previous_analysis"]["question"], "Ile zamówień?")
        self.assertEqual(payload["question"], "Tylko w sierpniu")

    def test_unsupported_browser_keeps_manual_input_available(self):
        app = self.app()
        self.component.return_value = update(app.session_state["dictation_target"], "", state="error", error="unsupported")
        app.run()
        self.assertFalse(app.chat_input[0].disabled)
        self.assertTrue(any("Google Chrome" in caption.value for caption in app.caption))
        self.client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
