"""Check recording validation and transcription with no real microphone or API."""

import struct
import unittest
import wave
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import httpx2
from openai import APITimeoutError, AuthenticationError, OpenAI, PermissionDeniedError

from src.ai import AIError, transcribe_audio
from src.audio import MAX_AUDIO_BYTES, validate_recording


def wav_recording(seconds: float = 1, silent: bool = False) -> bytes:
    """Build a small PCM test signal, never send it to a real service."""
    buffer = BytesIO()
    with wave.open(buffer, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(16000)
        recording.writeframes(struct.pack("<h", 0 if silent else 1000) * int(seconds * 16000))
    return buffer.getvalue()


class AudioTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = patch("src.ai.api_settings", return_value={"api_key": "test-only", "sql_model": "gpt-6-luna"})
        settings.start()
        self.addCleanup(settings.stop)
        client = patch("src.ai.create_client")
        self.client = client.start()
        self.addCleanup(client.stop)
        self.request = self.client.return_value.__enter__.return_value.audio.transcriptions.create
        self.request.return_value = SimpleNamespace(text=" Ile zamówień? ", usage=SimpleNamespace(seconds=2))

    def test_valid_recording_has_the_expected_duration(self) -> None:
        self.assertEqual(validate_recording(wav_recording(1.5)), 1.5)
        self.client.assert_not_called()

    def test_invalid_recordings_are_rejected_before_creating_a_client(self) -> None:
        recordings = [None, b"", b"not a WAV", b"x" * (MAX_AUDIO_BYTES + 1),
                      wav_recording(0), wav_recording(121), wav_recording(silent=True), wav_recording()[:-2]]
        for audio in recordings:
            with self.subTest(size=len(audio) if isinstance(audio, bytes) else None):
                with self.assertRaises(ValueError):
                    transcribe_audio(audio)
        self.client.assert_not_called()

    def test_whisper_receives_only_audio_and_optional_language(self) -> None:
        audio = wav_recording(1.5)
        result = transcribe_audio(audio, "pl")
        arguments = self.request.call_args.kwargs
        self.assertEqual(set(arguments), {"model", "file", "response_format", "language"})
        self.assertEqual(arguments["model"], "whisper-1")
        self.assertEqual(arguments["file"], ("question.wav", audio, "audio/wav"))
        self.assertEqual(arguments["language"], "pl")
        self.assertEqual(arguments["response_format"], "verbose_json")
        self.assertEqual(result["text"], "Ile zamówień?")
        self.assertEqual(result["usage"]["seconds"], 2)
        self.assertFalse(result["usage"]["duration_estimated"])
        self.assertAlmostEqual(result["usage"]["estimated_usd"], 0.0002)

    def test_auto_detection_omits_language_and_estimates_missing_duration_usage(self) -> None:
        self.request.return_value = SimpleNamespace(text="How many orders?", usage=None)
        result = transcribe_audio(wav_recording(1.5))
        self.assertNotIn("language", self.request.call_args.kwargs)
        self.assertEqual(result["usage"]["seconds"], 2)
        self.assertTrue(result["usage"]["duration_estimated"])

    def test_unsupported_language_does_not_send_audio(self) -> None:
        with self.assertRaises(ValueError):
            transcribe_audio(wav_recording(), "invalid")
        self.client.assert_not_called()

    def test_empty_transcript_keeps_usage_without_repeating_the_request(self) -> None:
        self.request.return_value.text = "   "
        with self.assertRaises(AIError) as caught:
            transcribe_audio(wav_recording())
        self.assertIn("Nie rozpoznano", str(caught.exception))
        self.assertEqual(caught.exception.usage["seconds"], 2)
        self.assertEqual(self.request.call_count, 1)

    def test_timeout_is_safe_to_display_and_is_not_retried(self) -> None:
        self.request.side_effect = APITimeoutError(request=httpx2.Request("POST", "https://example.invalid"))
        with self.assertRaises(AIError) as caught:
            transcribe_audio(wav_recording())
        self.assertIn("30 sekund", str(caught.exception))
        self.assertEqual(self.request.call_count, 1)

    def test_permissions_error_does_not_expose_provider_error_content(self) -> None:
        response = httpx2.Response(403, request=httpx2.Request("POST", "https://example.invalid"))
        self.request.side_effect = PermissionDeniedError("SECRET_PAYLOAD", response=response, body=None)
        with self.assertRaises(AIError) as caught:
            transcribe_audio(wav_recording())
        self.assertIn("/v1/audio/transcriptions", str(caught.exception))
        self.assertNotIn("SECRET_PAYLOAD", str(caught.exception))

    def test_generic_401_does_not_claim_the_key_is_invalid_or_expose_payload(self) -> None:
        response = httpx2.Response(401, request=httpx2.Request("POST", "https://example.invalid"))
        self.request.side_effect = AuthenticationError("SECRET_KEY_AND_PAYLOAD", response=response, body=None)
        with self.assertRaises(AIError) as caught:
            transcribe_audio(wav_recording())
        message = str(caught.exception)
        self.assertIn("HTTP 401", message)
        self.assertIn("/v1/audio/transcriptions", message)
        self.assertIn("nie przesądza", message)
        self.assertNotIn("SECRET_KEY_AND_PAYLOAD", message)
        self.assertEqual(self.request.call_count, 1)

    def test_explicit_invalid_key_code_has_specific_guidance_without_provider_message(self) -> None:
        response = httpx2.Response(401, request=httpx2.Request("POST", "https://example.invalid"))
        self.request.side_effect = AuthenticationError(
            "SECRET_KEY_AND_PAYLOAD", response=response, body={"code": "invalid_api_key"},
        )
        with self.assertRaises(AIError) as caught:
            transcribe_audio(wav_recording())
        self.assertIn("nieprawidłowy lub unieważniony klucz", str(caught.exception))
        self.assertNotIn("SECRET_KEY_AND_PAYLOAD", str(caught.exception))
        self.assertEqual(caught.exception.reason, "authentication")

    def test_missing_model_scope_is_recognized_with_null_code_and_both_statuses(self) -> None:
        for status, exception in ((401, AuthenticationError), (403, PermissionDeniedError)):
            with self.subTest(status=status):
                response = httpx2.Response(status, request=httpx2.Request("POST", "https://example.invalid"))
                body = {"code": None, "message": "Missing scopes: model.request. SECRET_KEY_AND_PAYLOAD"}
                self.request.side_effect = exception("SECRET_KEY_AND_PAYLOAD", response=response, body=body)
                with self.assertRaises(AIError) as caught:
                    transcribe_audio(wav_recording())
                self.assertIn("brak uprawnienia model.request", str(caught.exception))
                self.assertIn(f"HTTP {status}", str(caught.exception))
                self.assertNotIn("SECRET", str(caught.exception))
                self.assertEqual(caught.exception.reason, "missing_scope")

    def test_missing_scope_code_does_not_display_unknown_scope_or_provider_payload(self) -> None:
        response = httpx2.Response(401, request=httpx2.Request("POST", "https://example.invalid"))
        body = {"code": "missing_scope", "message": "Missing scopes: SECRET_SCOPE_AND_PAYLOAD"}
        self.request.side_effect = AuthenticationError("SECRET", response=response, body=body)
        with self.assertRaises(AIError) as caught:
            transcribe_audio(wav_recording())
        self.assertIn("brak wymaganych uprawnień", str(caught.exception))
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertNotIn("model.request", str(caught.exception))

    def test_real_sdk_sends_authenticated_multipart_to_official_endpoint_without_network(self) -> None:
        requests = []

        def respond(request):
            requests.append(request)
            self.assertEqual(request.method, "POST")
            self.assertEqual(str(request.url), "https://api.openai.com/v1/audio/transcriptions")
            self.assertEqual(request.headers["authorization"], "Bearer test-only")
            self.assertTrue(request.headers["content-type"].startswith("multipart/form-data; boundary="))
            self.assertIn(b'filename="question.wav"', request.content)
            self.assertIn(b"whisper-1", request.content)
            return httpx2.Response(200, json={"text": "Ile wypożyczeń?", "usage": {"seconds": 1}})

        self.client.return_value = OpenAI(
            api_key="test-only", organization="test-organization", project="test-project",
            base_url="https://api.openai.com/v1", max_retries=0,
            http_client=httpx2.Client(transport=httpx2.MockTransport(respond)),
        )
        result = transcribe_audio(wav_recording())
        self.assertEqual(result["text"], "Ile wypożyczeń?")
        self.assertEqual(len(requests), 1)


if __name__ == "__main__":
    unittest.main()
