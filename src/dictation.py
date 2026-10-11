"""Browser dictation updates, without OpenAI calls or audio uploads to Python."""

import streamlit.components.v2 as components

from src.db import PROJECT_ROOT

DICTATION_ERRORS = {
    "unsupported": "Ta przeglądarka nie obsługuje dyktowania. Otwórz aplikację w Google Chrome lub Edge.",
    "not-allowed": "Zezwól przeglądarce na korzystanie z mikrofonu i kliknij Mów ponownie.",
    "service-not-allowed": "Przeglądarka nie udostępnia rozpoznawania mowy. Spróbuj w Google Chrome.",
    "audio-capture": "Nie można odczytać mikrofonu. Sprawdź urządzenie i uprawnienia systemu.",
    "network": "Usługa rozpoznawania mowy nie odpowiada. Sprawdź połączenie z internetem.",
    "no-speech": "Nie wykryto mowy. Kliknij Mów i spróbuj ponownie.",
    "language-not-supported": "Przeglądarka nie obsługuje wybranego języka dyktowania.",
    "limit": "Osiągnięto limit 2000 znaków dyktowania. Sprawdź tekst przed wysłaniem.",
    "timeout": "Dyktowanie zatrzymano po 2 minutach. Możesz poprawić i wysłać tekst.",
    "unknown": "Nie udało się uruchomić dyktowania. Spróbuj ponownie lub wpisz pytanie.",
}


def dictation_component():
    """Register a small local component; no frontend build tools required."""
    assets = PROJECT_ROOT / "assets"
    return components.component(
        "live_dictation",
        html=(assets / "dictation.html").read_text(encoding="utf-8"),
        css=(assets / "dictation.css").read_text(encoding="utf-8"),
        js=(assets / "dictation.js").read_text(encoding="utf-8"),
    )


def render_dictation(target: str, disabled: bool = False) -> dict | None:
    """Receive text updates only. Starting the microphone happens in the browser."""
    result = dictation_component()(
        key="live_dictation",
        data={"target": target, "disabled": disabled, "max_chars": 2000, "max_seconds": 120},
        default={"update": None}, on_update_change=lambda: None,
    )
    # "update" also names a dict method; attribute access would return that
    # method rather than the text event received from the browser.
    return result.get("update")


def validate_dictation_update(update: object, target: str, previous: tuple | None = None) -> dict | None:
    """Reject stale, repeated or malformed browser events before changing a draft."""
    if not isinstance(update, dict) or update.get("target") != target:
        return None
    session, sequence, text = update.get("session"), update.get("sequence"), update.get("text")
    state = update.get("state")
    if (not isinstance(session, str) or not session or len(session) > 80
            or type(sequence) is not int or sequence < 1
            or not isinstance(text, str) or len(text) > 2000
            or not isinstance(state, str) or state not in {"starting", "listening", "idle", "error"}):
        return None
    if previous and session == previous[0] and sequence <= previous[1]:
        return None
    error = update.get("error")
    if error is not None and not isinstance(error, str):
        return None
    return {"event": (session, sequence), "text": text, "state": state,
            "error": DICTATION_ERRORS.get(error, DICTATION_ERRORS["unknown"] if error else None)}
