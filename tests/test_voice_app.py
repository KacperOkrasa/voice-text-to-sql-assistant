"""Exercise voice review and follow-ups with temporary history and a fake API."""

import json
import sqlite3
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.ai import AIError
from src.storage import load_conversations, register_database, save_conversation
from tests.test_ai import response
from tests.test_audio import wav_recording

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


class VoiceAppTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        storage = patch("src.storage.DEFAULT_STORAGE_PATH", self.directory / "history.db")
        storage.start()
        self.addCleanup(storage.stop)
        settings = patch("src.ai.api_settings", return_value={"api_key": "test-only", "sql_model": "gpt-6-luna"})
        settings.start()
        self.addCleanup(settings.stop)
        client = patch("src.ai.create_client")
        fake_client = client.start().return_value.__enter__.return_value
        self.addCleanup(client.stop)
        self.transcription = fake_client.audio.transcriptions.create
        self.transcription.return_value = SimpleNamespace(text="Ile zamówień?", usage=SimpleNamespace(seconds=1))
        self.sql_request = fake_client.responses.create
        self.sql_request.return_value = response("SELECT COUNT(*) AS total FROM orders")

    def app(self):
        return AppTest.from_file(str(APP_PATH), default_timeout=20).run()

    def open_recording(self, app):
        app.button(key="open_ai").click().run()
        app.button(key="open_voice").click().run()

    def transcribe_in_app(self, app, text="Ile zamówień?"):
        self.transcription.return_value.text = text
        self.open_recording(app)
        with patch("streamlit.audio_input", return_value=BytesIO(wav_recording())):
            app.run()
            app.button(key="transcribe_voice").click().run()
        self.assertFalse(app.exception)

    def test_opening_recorder_never_calls_the_api(self) -> None:
        app = self.app()
        self.open_recording(app)
        self.assertFalse(app.exception)
        self.assertEqual(len(app.get("audio_input")), 1)
        self.assertTrue(app.button(key="transcribe_voice").disabled)
        self.transcription.assert_not_called()
        self.sql_request.assert_not_called()

    def test_reviewed_question_is_sent_once_and_persists_without_audio(self) -> None:
        app = self.app()
        audio = BytesIO(wav_recording())
        self.open_recording(app)
        with patch("streamlit.audio_input", return_value=audio):
            app.run()
            app.button(key="transcribe_voice").click().run()
            self.sql_request.assert_not_called()
            app.run()
            self.assertTrue(app.button(key="transcribe_voice").disabled)
            app.text_area(key="voice_draft").set_value("Ile jest wszystkich zamówień?")
            app.button(key="send_voice").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(self.transcription.call_count, 1)
        self.assertEqual(self.sql_request.call_count, 1)
        payload = json.loads(self.sql_request.call_args.kwargs["input"])
        self.assertEqual(payload["question"], "Ile jest wszystkich zamówień?")
        entry = load_conversations()[0]["entries"][0]
        self.assertEqual(entry["input_method"], "voice")
        self.assertEqual(entry["source"], "text")
        self.assertEqual(entry["frame"].iloc[0, 0], 60)
        self.assertEqual(entry["transcription_usage"][0]["model"], "whisper-1")
        self.assertNotIn("audio", entry)
        self.assertNotIn("voice_recording_hash", entry)
        app.run()
        restored = self.app()
        self.assertFalse(restored.exception)
        self.assertEqual(restored.dataframe[0].value.iloc[0, 0], 60)
        self.assertTrue(any("Transkrypcja: whisper-1" in caption.value for caption in restored.caption))
        self.assertEqual(self.transcription.call_count, 1)
        self.assertEqual(self.sql_request.call_count, 1)

    def test_voice_reply_uses_the_pending_clarification(self) -> None:
        self.sql_request.side_effect = [response(None, "Za jaki okres?"), response("SELECT COUNT(*) FROM orders")]
        app = self.app()
        app.chat_input(key="sql_input").set_value("Ile zamówień?").run()
        self.transcribe_in_app(app, "Cały okres")
        with patch("streamlit.audio_input", return_value=BytesIO(wav_recording())):
            app.button(key="send_voice").click().run()
        self.assertFalse(app.exception)
        payload = json.loads(self.sql_request.call_args.kwargs["input"])
        self.assertEqual(payload["question"], "Cały okres")
        self.assertEqual(payload["clarification_context"], [
            {"question": "Ile zamówień?", "clarification": "Za jaki okres?"},
        ])

    def test_text_followup_uses_the_completed_voice_analysis(self) -> None:
        first_sql = "SELECT customer_id FROM customers ORDER BY customer_id LIMIT 5"
        self.sql_request.side_effect = [response(first_sql), response(first_sql.replace("5", "10"))]
        app = self.app()
        self.transcribe_in_app(app, "Pokaż pierwszych pięciu klientów")
        with patch("streamlit.audio_input", return_value=BytesIO(wav_recording())):
            app.button(key="send_voice").click().run()
        app.chat_input(key="sql_input").set_value("A teraz dziesięciu").run()
        self.assertFalse(app.exception)
        payload = json.loads(self.sql_request.call_args.kwargs["input"])
        self.assertEqual(payload["previous_analysis"], {
            "question": "Pokaż pierwszych pięciu klientów", "sql": first_sql,
        })
        self.assertNotIn("transcription_usage", payload)

    def test_transcription_failure_is_not_automatically_retried(self) -> None:
        app = self.app()
        self.open_recording(app)
        self.transcription.side_effect = AIError("Brak połączenia")
        with patch("streamlit.audio_input", return_value=BytesIO(wav_recording())):
            app.run()
            app.button(key="transcribe_voice").click().run()
            app.run()
            self.assertEqual(self.transcription.call_count, 1)
            self.assertFalse(app.text_area)
            self.transcription.side_effect = None
            app.button(key="transcribe_voice").click().run()
            self.assertEqual(app.text_area(key="voice_draft").value, "Ile zamówień?")
        self.assertFalse(app.exception)
        self.assertEqual(self.transcription.call_count, 2)
        self.sql_request.assert_not_called()

    def test_cancelling_a_transcript_does_not_execute_or_save_a_question(self) -> None:
        app = self.app()
        self.transcribe_in_app(app)
        app.button(key="cancel_voice").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(load_conversations()[0]["entries"])
        self.assertIsNone(app.session_state.active_dialog)
        self.sql_request.assert_not_called()

    def test_changed_database_prevents_sending_a_stale_voice_draft(self) -> None:
        custom = self.directory / "custom.db"
        with sqlite3.connect(custom) as connection:
            connection.execute("CREATE TABLE readings (value INTEGER)")
        register_database({"id": "custom", "name": "Pomiary", "path": str(custom)})
        app = self.app()
        self.transcribe_in_app(app)
        changed = load_conversations()[0]
        changed["database_id"] = "custom"
        save_conversation(changed)
        with patch("streamlit.audio_input", return_value=BytesIO(wav_recording())):
            app.button(key="send_voice").click().run()
        self.assertFalse(app.exception)
        self.sql_request.assert_not_called()
        self.assertFalse(load_conversations()[0]["entries"])

    def test_empty_or_long_review_stays_editable_without_running_sql(self) -> None:
        app = self.app()
        self.transcribe_in_app(app)
        with patch("streamlit.audio_input", return_value=BytesIO(wav_recording())):
            for text in ("   ", "a" * 2001):
                app.text_area(key="voice_draft").set_value(text)
                app.button(key="send_voice").click().run()
                self.assertFalse(app.exception)
                self.assertTrue(any("2000 znaków" in error.value for error in app.error))
                self.assertEqual(app.text_area(key="voice_draft").value, text)
        self.sql_request.assert_not_called()
        self.assertEqual(self.transcription.call_count, 1)

    def test_retry_of_failed_voice_analysis_reuses_text_without_transcribing_again(self) -> None:
        self.sql_request.side_effect = [response(status="incomplete", reason="max_output_tokens"), response()]
        app = self.app()
        self.transcribe_in_app(app)
        with patch("streamlit.audio_input", return_value=BytesIO(wav_recording())):
            app.button(key="send_voice").click().run()
        app.button(key="retry_1").click().run()
        self.assertFalse(app.exception)
        history = load_conversations()[0]["entries"]
        self.assertEqual(history[1]["retry_of"], history[0]["id"])
        self.assertEqual(history[1]["question"], "Ile zamówień?")
        self.assertNotIn("transcription_usage", history[1])
        self.assertEqual(self.sql_request.call_count, 2)
        self.assertEqual(self.transcription.call_count, 1)

    def test_recording_requires_configuration_but_presets_work_without_a_key(self) -> None:
        with patch("src.ai.api_settings", return_value={"api_key": "", "sql_model": "gpt-6-luna"}):
            app = self.app()
            self.open_recording(app)
            self.assertTrue(app.button(key="transcribe_voice").disabled)
            app.button(key="cancel_voice").click().run()
            app.button(key="Przychód miesięczny").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.dataframe[0].value), 6)
        self.sql_request.assert_not_called()
        self.transcription.assert_not_called()


if __name__ == "__main__":
    unittest.main()
