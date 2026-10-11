"""Validate short microphone recordings locally before any API request."""

import wave
from io import BytesIO

MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_AUDIO_SECONDS = 120


def validate_recording(audio: bytes) -> float:
    """Return the duration of a complete PCM WAV, or reject it without uploading."""
    if not isinstance(audio, bytes) or not audio:
        raise ValueError("Najpierw nagraj pytanie.")
    if len(audio) > MAX_AUDIO_BYTES:
        raise ValueError("Nagranie może mieć maksymalnie 8 MB. Nagraj krótsze pytanie.")
    try:
        with wave.open(BytesIO(audio), "rb") as recording:
            frames = recording.getnframes()
            rate = recording.getframerate()
            channels = recording.getnchannels()
            width = recording.getsampwidth()
            if channels not in (1, 2) or width != 2 or not 8000 <= rate <= 48000:
                raise ValueError("Nagranie musi być plikiem WAV PCM 16-bit z mikrofonu.")
            duration = frames / rate
            if not 0 < duration <= MAX_AUDIO_SECONDS:
                raise ValueError("Nagranie jest puste lub trwa dłużej niż 120 sekund.")
            samples = recording.readframes(frames)
            if len(samples) != frames * channels * width:
                raise ValueError("Nagranie jest niekompletne. Nagraj pytanie ponownie.")
    except (wave.Error, EOFError):
        raise ValueError("Nie można odczytać nagrania WAV. Nagraj pytanie ponownie.") from None
    if not any(samples):
        raise ValueError("Nagranie zawiera ciszę. Sprawdź mikrofon i nagraj pytanie ponownie.")
    return duration
