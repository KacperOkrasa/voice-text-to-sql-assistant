"""Local Streamlit demo: SQLite analytics, result tables and validated SQL."""

from hashlib import sha256
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from data.seed import create_demo_database
from src.ai import AIError, MAX_QUESTION_LENGTH, api_configured, transcribe_audio
from src.analysis import analyze_question, get_clarification_context, get_previous_analysis
from src.dictation import render_dictation, validate_dictation_update
from src.db import (
    DEFAULT_DB_PATH, PROJECT_ROOT, execute_query, get_database_summary, get_relationships,
    get_schema, get_table_columns, get_table_page, get_tables,
)
from src.presentation import browser_frame, display_timestamp, export_csv, format_value, style_table
from src.results import ResultLimitError, check_frame, result_frame
from src.security import UnsafeSQLError
from src.storage import (
    add_analysis, archive_database, create_conversation, delete_conversation,
    list_imported_databases, load_active_conversation, load_conversation, load_conversations, rename_database,
    save_active_conversation, save_analysis, save_conversation,
)
from src.uploads import import_database


ROW_LIMIT = 1000
EXAMPLE_QUERIES = {
    "Przychód miesięczny": """
        SELECT strftime('%Y-%m', o.order_date) AS miesiac,
               ROUND(SUM(i.quantity * i.unit_price_cents) / 100.0, 2) AS przychod_pln
        FROM orders o
        JOIN order_items i ON i.order_id = o.order_id
        WHERE o.status = 'delivered'
        GROUP BY miesiac ORDER BY miesiac;
    """,
    "Najlepsze produkty": """
        SELECT p.name AS produkt, SUM(i.quantity) AS sprzedane_sztuki,
               ROUND(SUM(i.quantity * i.unit_price_cents) / 100.0, 2) AS przychod_pln
        FROM order_items i
        JOIN products p ON p.product_id = i.product_id
        JOIN orders o ON o.order_id = i.order_id
        WHERE o.status = 'delivered'
        GROUP BY p.product_id, p.name
        ORDER BY sprzedane_sztuki DESC, produkt LIMIT 8;
    """,
    "Sprzedaż wg miast": """
        SELECT c.city AS miasto,
               ROUND(SUM(i.quantity * i.unit_price_cents) / 100.0, 2) AS przychod_pln
        FROM customers c
        JOIN orders o ON o.customer_id = c.customer_id
        JOIN order_items i ON i.order_id = o.order_id
        WHERE o.status = 'delivered'
        GROUP BY c.city ORDER BY przychod_pln DESC;
    """,
    "Statusy zamówień": """
        SELECT status, COUNT(*) AS liczba_zamowien
        FROM orders GROUP BY status ORDER BY liczba_zamowien DESC;
    """,
}


EXAMPLE_QUESTIONS = {
    "Przychód miesięczny": "Jaki był przychód w poszczególnych miesiącach?",
    "Najlepsze produkty": "Które produkty sprzedawały się najlepiej?",
    "Sprzedaż wg miast": "Z których miast pochodził największy przychód?",
    "Statusy zamówień": "Ile zamówień jest w każdym statusie?",
}
EXAMPLE_ICONS = {
    "Przychód miesięczny": ":material/trending_up:",
    "Najlepsze produkty": ":material/shopping_bag:",
    "Sprzedaż wg miast": ":material/location_on:",
    "Statusy zamówień": ":material/receipt_long:",
}


def start_conversation() -> None:
    """Start an empty conversation, reusing the current one if it is still empty."""
    if not refresh_active_conversation():
        return
    conversations = st.session_state.conversations
    active = next(item for item in conversations if item["id"] == st.session_state.active_conversation)
    if not active["entries"]:
        return
    available_ids = {item["id"] for item in databases()}
    database_id = active["database_id"] if active["database_id"] in available_ids else "demo"
    conversation = create_conversation(database_id)
    conversations.append(conversation)
    select_conversation(conversation["id"])


def select_conversation(conversation_id: int) -> None:
    if st.session_state.get("active_dialog") == "voice":
        close_dialog()
    st.session_state.active_conversation = conversation_id
    save_active_conversation(conversation_id)


def remove_chat(conversation_id: int) -> None:
    """Delete saved results and keep the app on an existing or new conversation."""
    conversation = next(item for item in st.session_state.conversations if item["id"] == conversation_id)
    delete_conversation(conversation_id)
    remaining = [item for item in st.session_state.conversations if item["id"] != conversation_id]
    st.session_state.conversations = remaining
    if st.session_state.active_conversation == conversation_id:
        if not remaining:
            available_ids = {item["id"] for item in databases()}
            database_id = conversation["database_id"] if conversation["database_id"] in available_ids else "demo"
            remaining = [create_conversation(database_id)]
            st.session_state.conversations.extend(remaining)
        select_conversation(remaining[-1]["id"])
        st.session_state.pop("pending_example", None)
        st.session_state.pop("pending_retry", None)
        close_dialog()


def queue_example(label: str) -> None:
    """Queue only the name of a built-in, trusted demonstration."""
    st.session_state.pending_example = label


def active_conversation() -> dict:
    return next(
        item for item in st.session_state.conversations
        if item["id"] == st.session_state.active_conversation
    )


def refresh_active_conversation() -> bool:
    """Use persisted messages from other tabs, retaining session-only results."""
    cached = active_conversation()
    saved = load_conversation(cached["id"])
    if saved is None:
        return False
    local_resets = st.session_state.get("local_context_resets", set())
    for entry in saved["entries"]:
        if entry["id"] in local_resets:
            entry["context_cancelled"] = True
    # A failed history write must never discard a paid response or repeat it.
    saved["entries"].extend(entry for entry in cached["entries"] if entry.get("unsaved"))
    cached.update(saved)
    return True


def clear_analysis_context() -> None:
    """Clear follow-up or clarification context without deleting any messages."""
    if not refresh_active_conversation():
        return
    entry = active_conversation()["entries"][-1]
    entry["context_cancelled"] = True
    if not entry.get("unsaved"):
        try:
            save_analysis(entry)
        except (OSError, sqlite3.Error):
            st.session_state.setdefault("local_context_resets", set()).add(entry["id"])
            st.session_state.history_notice = "Nie można zapisać zmiany na dysku. Nowe pytanie jest dostępne w tej sesji."


def queue_retry(entry_id: int | str) -> None:
    """Schedule a single explicit retry of this saved question in its own chat."""
    st.session_state.pending_retry = (active_conversation()["id"], entry_id)


def use_database(database_id: str) -> None:
    """Keep each conversation tied to one database."""
    conversation = active_conversation()
    if conversation["database_id"] == database_id:
        return
    if st.session_state.get("active_dialog") == "voice":
        close_dialog()
    if conversation["entries"]:
        conversation = create_conversation(database_id)
        st.session_state.conversations.append(conversation)
        select_conversation(conversation["id"])
    else:
        conversation["database_id"] = database_id
        save_conversation(conversation)


def change_database() -> None:
    if refresh_active_conversation():
        use_database(st.session_state.database_selection)


def databases(include_archived: bool = False) -> list[dict]:
    demo = {"id": "demo", "name": "E-commerce (demo)", "path": str(DEFAULT_DB_PATH), "archived": False}
    return [demo, *list_imported_databases(include_archived)]


def close_dialog() -> None:
    if st.session_state.get("active_dialog") == "voice":
        clear_voice_recording()
        st.session_state.pop("voice_target", None)
        st.session_state.voice_recording_version = st.session_state.get("voice_recording_version", 0) + 1
    st.session_state.active_dialog = None


def clear_voice_recording() -> None:
    """Forget the current transcript and usage without saving the recording."""
    for key in ("voice_recording_hash", "voice_draft", "voice_done", "voice_error", "voice_usage"):
        st.session_state.pop(key, None)


def open_voice_input() -> None:
    """Bind the voice draft to this conversation and database."""
    clear_voice_recording()
    st.session_state.voice_recording_version = st.session_state.get("voice_recording_version", 0) + 1
    conversation = active_conversation()
    st.session_state.voice_target = (conversation["id"], conversation["database_id"])
    st.session_state.active_dialog = "voice"


def render_transcription_usage(usage: list[dict]) -> None:
    if usage:
        seconds = sum(item["seconds"] for item in usage)
        cost = sum(item["estimated_usd"] for item in usage)
        st.caption(f"Transkrypcja: whisper-1 · {len(usage)} żądanie/żądania · {seconds:g} s "
                   f"· szacowany koszt {cost:.6f} USD, bez podatków.")


@st.dialog("Pytanie głosowe", width="medium", on_dismiss=close_dialog)
def voice_input(database: dict) -> None:
    """Record, explicitly transcribe once, then review before running an analysis."""
    conversation = active_conversation()
    target = (conversation["id"], conversation["database_id"])
    if st.session_state.get("voice_target") != target or database.get("archived"):
        st.info("Rozmowa lub baza została zmieniona. Zamknij okno i otwórz nagrywanie ponownie.")
        return
    configured = api_configured()
    st.caption(f"Baza: {database['name']}")
    st.write("Nagraj pytanie po polsku lub angielsku, a następnie sprawdź rozpoznany tekst.")
    st.caption("„Zamień na tekst” wysyła nagranie do OpenAI i może naliczyć koszt. "
               "Limit: 2 minuty i 8 MB. Pliku audio nie zapisujemy w historii.")
    languages = {"auto": "Wykryj automatycznie", "pl": "Polski", "en": "English"}
    language = st.selectbox("Język nagrania", list(languages), format_func=languages.get, key="voice_language")
    recording = st.audio_input(
        "Nagraj pytanie", sample_rate=16000,
        key=f"voice_recording_{st.session_state.voice_recording_version}", disabled=not configured,
    )
    audio = recording.getvalue() if recording is not None else None
    fingerprint = sha256(audio).hexdigest() if audio is not None else None
    if fingerprint != st.session_state.get("voice_recording_hash"):
        clear_voice_recording()
        st.session_state.voice_recording_hash = fingerprint
    if not configured:
        st.info("Najpierw dodaj klucz API w lokalnym .env. Instrukcja jest w przycisku AI.")
    if st.button("Zamień na tekst", key="transcribe_voice", icon=":material/transcribe:",
                 disabled=audio is None or not configured or st.session_state.get("voice_done", False)) \
            and not st.session_state.get("voice_done"):
        try:
            with st.spinner("Rozpoznaję nagranie…"):
                result = transcribe_audio(audio, None if language == "auto" else language)
            st.session_state.voice_draft = result["text"]
            st.session_state.voice_usage = [*st.session_state.get("voice_usage", []), result["usage"]]
            st.session_state.voice_done = True
            st.session_state.pop("voice_error", None)
        except (AIError, ValueError) as error:
            st.session_state.voice_error = str(error)
            if isinstance(error, AIError) and error.usage is not None:
                st.session_state.voice_usage = [*st.session_state.get("voice_usage", []), error.usage]
    if st.session_state.get("voice_error"):
        st.error(st.session_state.voice_error)
        st.caption("Ponowienie wymaga kliknięcia i może naliczyć kolejny koszt.")
    render_transcription_usage(st.session_state.get("voice_usage", []))
    if st.session_state.get("voice_done"):
        with st.form("voice_question_form"):
            question = st.text_area("Sprawdź i popraw pytanie", key="voice_draft", height=120)
            st.caption("Wyślij maksymalnie 2000 znaków. Analiza użyje kontekstu tej rozmowy.")
            send = st.form_submit_button("Wyślij pytanie", key="send_voice", type="primary")
        if send:
            if not question.strip() or len(question.strip()) > MAX_QUESTION_LENGTH:
                st.error("Pytanie musi mieć od 1 do 2000 znaków. Popraw tekst przed wysłaniem.")
            else:
                st.session_state.pending_voice = {
                    "target": target, "question": question.strip(),
                    "transcription_usage": st.session_state.get("voice_usage", []).copy(),
                }
                close_dialog()
                st.rerun()
    if st.button("Anuluj", key="cancel_voice", type="tertiary"):
        close_dialog()
        st.rerun()


@st.dialog("Połącz AI", width="medium", on_dismiss=close_dialog)
def configure_ai() -> None:
    """Explain local key setup without accepting or displaying credentials in the UI."""
    if api_configured():
        st.success("Klucz jest wpisany lokalnie. Pierwsze pytanie sprawdzi połączenie z API.")
    else:
        st.info("Pytania do AI wymagają klucza API. Gotowe przykłady działają bez klucza.")
    st.markdown(
        "1. Zaloguj się do [OpenAI Platform](https://platform.openai.com/).\n"
        "2. W swoim projekcie utwórz [klucz API](https://platform.openai.com/api-keys).\n"
        "3. Dodaj klucz do lokalnego pliku `.env` w katalogu projektu i zapisz plik.\n"
        "4. Odśwież stronę, wybierz bazę i wyślij pierwsze pytanie."
    )
    st.code("OPENAI_API_KEY=tu_wpisz_swój_klucz\nOPENAI_SQL_MODEL=gpt-6-luna", language="dotenv")
    st.caption("Klucz wpisuj wyłącznie do .env. Nie publikuj go ani nie przesyłaj w rozmowie.")
    st.write("Do AI trafiają pytanie i schemat: nazwy tabel, kolumn, typy oraz relacje. "
             "Kontekst rozmowy obejmuje ostatnie pytanie i SQL wygenerowany przez AI, "
             "a przy doprecyzowaniu także związane z nim pytania i odpowiedzi. "
             "Plik bazy, rekordy i wyniki pozostają lokalnie.")
    st.caption("Żądania mają store=False. Nie oznacza to braku logów po stronie dostawcy. "
               "API jest rozliczane osobno; saldo i zużycie sprawdzisz w OpenAI Platform.")
    st.caption("Przycisk Mów wpisuje tekst na żywo przez usługę rozpoznawania mowy przeglądarki. "
               "Głos może przetwarzać jej dostawca, np. Google. OpenAI otrzymuje pytanie po wysłaniu.")
    with st.expander("Alternatywa: transkrypcja gotowego nagrania"):
        st.caption("Whisper wysyła nagranie do OpenAI i jest płatny. "
                   "Wymaga dostępu klucza do /v1/audio/transcriptions.")
        st.button("Nagraj pytanie", key="open_voice", icon=":material/mic:", on_click=open_voice_input)


@st.dialog("O danych", width="large", on_dismiss=close_dialog)
def show_data(database: dict) -> None:
    st.caption(database["name"])
    try:
        tables = get_tables(database["path"])
        if not tables:
            st.info("Ta baza nie zawiera tabel ani widoków.")
            return
        table = st.selectbox("Tabela lub widok", tables, key=f"table_{database['id']}")
        data_tab, columns_tab, relations_tab, privacy_tab = st.tabs(["Dane", "Kolumny", "Relacje", "Schemat dla AI"])
        with relations_tab:
            relationships = get_relationships(database["path"])
            if relationships:
                st.dataframe(pd.DataFrame(relationships), hide_index=True, width="stretch")
            else:
                st.info("Ta baza nie ma zadeklarowanych kluczy obcych.")
        with privacy_tab:
            st.info("To schemat wysyłany z pytaniem do AI: nazwy, typy i relacje, bez rekordów. "
                    "Samo otwarcie tej zakładki nie wywołuje API.")
            st.caption("Pomijamy wartości DEFAULT, warunki CHECK i treść widoków. Nazwy mogą ujawniać przeznaczenie bazy.")
            st.code(get_schema(database["path"]), language="json")
        with columns_tab:
            st.dataframe(
                pd.DataFrame(get_table_columns(table, database["path"])),
                hide_index=True, width="stretch",
            )
        with data_tab:
            page = st.number_input(
                "Strona (50 wierszy)", min_value=1, step=1,
                key=f"page_{database['id']}_{table}",
            )
            rows, columns, count = get_table_page(table, database["path"], page=page)
            total_pages = max(1, (count + 49) // 50)
            st.caption(f"{count} wierszy · {len(columns)} kolumn · strona {page}/{total_pages}")
            if count and not rows:
                st.info("Brak wierszy na tej stronie. Wybierz wcześniejszą stronę.")
            st.dataframe(browser_frame(result_frame(rows, columns)), hide_index=True, width="stretch")
            st.caption("Nagłówki to dokładne nazwy kolumn, których możesz używać w SQL.")
    except (sqlite3.Error, FileNotFoundError, ValueError) as error:
        st.error(f"Nie można wyświetlić danych: {error}")


def upload_database() -> None:
    st.write("Wybierz bazę, na której chcesz wykonywać analizy.")
    file = st.file_uploader(
        "Plik bazy", type=["db", "sqlite", "sqlite3"], max_upload_size=50,
        key="database_upload",
    )
    st.caption("Lokalna kopia · do 50 MB · zapytania tylko do odczytu")
    st.info("Plik i wyniki pozostają na tym komputerze. Przy pytaniu do AI wysyłamy treść pytania "
            "i schemat bazy, bez rekordów.")
    if st.button("Dodaj i wybierz bazę", key="add_database", type="primary", disabled=file is None):
        try:
            database = import_database(file.name, file.getvalue())
            use_database(database["id"])
            close_dialog()
            st.rerun()
        except (ValueError, OSError, sqlite3.Error) as error:
            st.error(str(error))


@st.dialog("Bazy danych", width="medium", on_dismiss=close_dialog)
def manage_databases() -> None:
    available_tab, upload_tab, trash_tab = st.tabs(["Dostępne", "Dodaj bazę", "Kosz"])
    all_databases = databases(include_archived=True)
    with upload_tab:
        upload_database()
    with available_tab:
        st.caption("Lokalne kopie baz. Zmiana nazwy nie zmienia tabel ani oryginalnego pliku.")
        for database in all_databases:
            if database.get("archived"):
                continue
            with st.container(border=True):
                st.write(database["name"])
                try:
                    summary = get_database_summary(database["path"])
                    size = f"{summary['size_bytes'] / (1024 * 1024):.2f}".replace(".", ",")
                    st.caption(f"Tabele: {summary['tables']} · Widoki: {summary['views']} · {size} MB")
                except (FileNotFoundError, OSError, sqlite3.Error):
                    st.warning("Plik jest niedostępny. Wgraj tę samą bazę ponownie.")
                if database["id"] == "demo":
                    st.caption("Wbudowana baza demonstracyjna")
                    continue
                st.caption(f"Import: {display_timestamp(database['imported_at'])}")
                with st.form(f"rename_database_{database['id']}"):
                    name = st.text_input(
                        "Nazwa bazy", value=database["name"], max_chars=80, key=f"database_name_{database['id']}",
                    )
                    if st.form_submit_button("Zapisz nazwę", key=f"save_database_name_{database['id']}"):
                        try:
                            rename_database(database["id"], name)
                            st.rerun()
                        except ValueError as error:
                            st.error(str(error))
                if st.button("Usuń z listy", key=f"remove_database_{database['id']}", icon=":material/delete_outline:"):
                    archive_database(database["id"])
                    if active_conversation()["database_id"] == database["id"]:
                        use_database("demo")
                    st.rerun()
    with trash_tab:
        st.caption("Pliki i zapisane wyniki zostają lokalnie. Usunięcie z listy można cofnąć.")
        removed = [item for item in all_databases if item.get("archived")]
        if not removed:
            st.info("Kosz baz jest pusty.")
        for database in removed:
            with st.container(border=True):
                st.write(database["name"])
                if st.button("Przywróć bazę", key=f"restore_database_{database['id']}"):
                    archive_database(database["id"], archived=False)
                    st.rerun()


def example_buttons() -> None:
    labels = list(EXAMPLE_QUERIES)
    with st.container(key="suggestions"):
        for start in (0, 2):
            for column, label in zip(st.columns(2), labels[start:start + 2]):
                column.button(
                    label, key=label, icon=EXAMPLE_ICONS[label], width="stretch",
                    on_click=queue_example, args=(label,),
                )


def analyze_example(title: str) -> dict:
    """Run one built-in demo query; user input never supplies SQL here."""
    sql = EXAMPLE_QUERIES[title]
    started = time.perf_counter()
    entry = {
        "title": title,
        "sql": sql.strip(),
        "created_at": datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d %H:%M"),
        "frame": pd.DataFrame(),
        "error": None,
        "truncated": False,
    }
    try:
        rows, columns = execute_query(sql, db_path=DEFAULT_DB_PATH, row_limit=ROW_LIMIT)
        frame = result_frame(rows, columns)
        entry["truncated"] = len(frame) > ROW_LIMIT
        entry["frame"] = frame.head(ROW_LIMIT)
    except (UnsafeSQLError, ResultLimitError) as error:
        entry["error"] = str(error)
    except FileNotFoundError:
        entry["error"] = "Plik bazy jest niedostępny. Wgraj go ponownie lub wybierz inną bazę."
    except sqlite3.Error as error:
        if "interrupted" in str(error):
            entry["error"] = "Zapytanie trwało zbyt długo. Spróbuj zawęzić analizę."
        else:
            entry["error"] = "Nie udało się wykonać przykładowej analizy. Spróbuj ponownie później."
    entry["elapsed"] = time.perf_counter() - started
    return entry


def render_data(entry: dict) -> None:
    frame = entry["frame"]
    try:
        check_frame(frame)
    except ResultLimitError as error:
        st.error(str(error))
        return
    if entry["truncated"]:
        st.warning(f"Pokazuję pierwsze {ROW_LIMIT} wierszy. CSV obejmuje ten sam zakres.")
    if frame.empty:
        st.info("Zapytanie nie zwróciło danych. Spróbuj zmienić warunki analizy.")
    else:
        try:
            st.dataframe(style_table(frame), width="stretch", hide_index=True)
        except (ValueError, TypeError, OverflowError):
            st.error("Nie można wyświetlić tej tabeli. Pozostałe wyniki są dostępne.")
            return

    elapsed = format_value(entry["elapsed"], "czas")
    st.caption(f"{display_timestamp(entry['created_at'])} · {len(frame)} wierszy · {elapsed} s")
    with st.expander("SQL i eksport"):
        st.code(entry["sql"], language="sql")
        if entry.get("repaired"):
            st.caption("AI poprawiło SQL po błędzie bazy — jedna próba.")
        render_api_usage(entry)
        if not frame.empty:
            st.download_button(
                "Pobierz CSV do arkusza", export_csv(frame),
                file_name=f"analiza_{entry['id']}.csv", mime="text/csv",
                key=f"csv_{entry['id']}", on_click="ignore",
            )
            st.caption("CSV do arkusza: jednostki jak w tabeli; tekst przypominający formuły "
                       "i duże identyfikatory otrzymują apostrof ochronny.")
            with st.expander("Eksport surowych danych"):
                st.caption("Oryginalne nazwy i wartości, np. grosze w kolumnach _cents. "
                           "Tekst nie jest zabezpieczony przed interpretacją jako formuła. "
                           "Ten plik służy do przetwarzania w kodzie, nie do otwierania w arkuszu.")
                st.download_button(
                    "Pobierz surowy CSV", export_csv(frame, spreadsheet=False),
                    file_name=f"analiza_{entry['id']}_raw.csv", mime="text/csv",
                    key=f"csv_raw_{entry['id']}", on_click="ignore",
                )


def render_api_usage(entry: dict) -> None:
    """Show local usage counters; this estimate is not a provider billing balance."""
    render_transcription_usage(entry.get("transcription_usage", []))
    usage = entry.get("api_usage", [])
    if usage:
        tokens = sum(item["input_tokens"] + item["output_tokens"] for item in usage)
        models = ", ".join(dict.fromkeys(item["model"] for item in usage))
        st.caption(f"AI: {models} · {len(usage)} żądanie/żądania · {tokens} tokenów")
        costs = [item["estimated_usd"] for item in usage]
        if all(cost is not None for cost in costs):
            st.caption(f"Szacowany koszt: {sum(costs):.6f} USD, bez podatków; "
                       "nie uwzględnia rozliczeń cache ani żądań bez informacji o zużyciu.")
    elif entry.get("source") == "text":
        st.caption("Brak zapisanej informacji o zużyciu tokenów dla tej próby. Nie oznacza to zerowego kosztu.")


def render_result(entry: dict, expanded: bool) -> None:
    if entry.get("source") == "corrupt":
        st.warning(entry["error"])
        return
    with st.chat_message("user"):
        if entry.get("question"):
            if entry.get("source") == "blocked":
                st.text(entry["question"])
            else:
                st.write(entry["question"])
            if entry.get("input_method") == "voice":
                st.caption("Pytanie z mikrofonu")
        elif entry["title"] in EXAMPLE_QUESTIONS:
            st.write(EXAMPLE_QUESTIONS[entry["title"]])
        else:
            st.code(entry["sql"], language="sql")

    with st.chat_message("assistant", avatar=":material/query_stats:"):
        if entry["error"]:
            st.error(entry["error"])
            st.caption(display_timestamp(entry['created_at']))
            render_api_usage(entry)
            if entry["sql"]:
                with st.expander("Podgląd SQL"):
                    st.code(entry["sql"], language="sql")
            if entry.get("question") and entry.get("source") == "text":
                st.button(
                    "Spróbuj ponownie", key=f"retry_{entry['id']}", icon=":material/refresh:",
                    on_click=queue_retry, args=(entry["id"],), disabled=st.session_state.database_archived,
                )
                st.caption("Ponowienie zachowa pytanie i jego kontekst. Nowe żądanie może być płatne.")
        elif entry.get("clarification"):
            st.write(entry["clarification"])
            if expanded and not (entry.get("context_cancelled") or entry.get("clarification_cancelled")):
                st.caption("Możesz odpowiedzieć krótko w polu poniżej.")
            render_api_usage(entry)
        else:
            st.markdown('<div class="result-title">Wyniki</div>', unsafe_allow_html=True)
            if expanded:
                render_data(entry)
            else:
                with st.expander("Pokaż wynik"):
                    render_data(entry)


def main() -> None:
    st.set_page_config(
        page_title="SQL Assistant", page_icon=":material/query_stats:", layout="wide",
    )
    stylesheet = (PROJECT_ROOT / "assets" / "styles.css").read_text(encoding="utf-8")
    st.markdown(f"<style>{stylesheet}</style>", unsafe_allow_html=True)

    if not DEFAULT_DB_PATH.exists():
        try:
            create_demo_database()
        except FileExistsError:
            pass  # Another Streamlit session may have created the demo first.

    if st.session_state.get("storage_loaded") != 3:
        try:
            saved = load_conversations()
            # Preserve open chats from the earlier version when hot-reloading.
            for previous in st.session_state.get("conversations", []):
                if "database_id" not in previous:
                    migrated = create_conversation()
                    migrated["title"] = previous["title"]
                    for entry in previous["entries"]:
                        entry["id"] = add_analysis(migrated["id"], entry)
                        migrated["entries"].append(entry)
                    save_conversation(migrated)
                    saved.append(migrated)
            if not saved:
                saved.append(create_conversation())
            st.session_state.conversations = saved
            remembered = st.session_state.get("active_conversation") or load_active_conversation()
            valid_ids = [item["id"] for item in saved]
            select_conversation(remembered if remembered in valid_ids else valid_ids[-1])
            st.session_state.storage_loaded = 3
        except (OSError, sqlite3.Error, ValueError) as error:
            st.error(f"Nie można odczytać zapisanej historii: {error}")
            st.stop()

    try:
        conversation_available = refresh_active_conversation()
    except (OSError, sqlite3.Error, ValueError) as error:
        st.error(f"Nie można odczytać aktualnej rozmowy: {error}")
        st.stop()

    if not conversation_available:
        # Another open tab may have removed this chat since this session loaded it.
        st.session_state.conversations = load_conversations() or [create_conversation()]
        select_conversation(st.session_state.conversations[-1]["id"])
        st.session_state.pop("pending_example", None)
        st.session_state.pop("pending_retry", None)
        st.session_state.pop("pending_voice", None)
        close_dialog()
        st.session_state.history_notice = "Ta rozmowa została usunięta w innym oknie. Otworzyłem aktualną historię."
        st.rerun()

    available = databases(include_archived=True)
    by_id = {item["id"]: item for item in available}
    st.session_state.database_archived = bool(by_id[active_conversation()["database_id"]].get("archived"))
    example_label = st.session_state.pop("pending_example", None)
    if example_label not in EXAMPLE_QUERIES:
        example_label = None
    submitted = (EXAMPLE_QUESTIONS[example_label], example_label, "demo") if example_label else None
    # Discard an editor action queued by an older version during a hot reload.
    st.session_state.pop("pending_request", None)
    if st.session_state.get("active_dialog") == "sql":
        close_dialog()
    pending_retry = st.session_state.pop("pending_retry", None)
    voice_request = st.session_state.pop("pending_voice", None)
    if voice_request:
        target = (active_conversation()["id"], active_conversation()["database_id"])
        if voice_request["target"] == target and not st.session_state.database_archived:
            example_label = None
            submitted = (voice_request["question"], "Własna analiza", target[1])
        else:
            voice_request = None
            st.session_state.history_notice = "Rozmowa lub baza została zmieniona. Pytanie głosowe nie zostało wysłane."
    retry_entry = None
    if pending_retry and pending_retry[0] == active_conversation()["id"] and not st.session_state.database_archived:
        retry_entry = next((entry for entry in active_conversation()["entries"]
                            if entry["id"] == pending_retry[1] and entry.get("error")
                            and entry.get("source") == "text" and entry.get("question")), None)
        if retry_entry:
            example_label = None
            submitted = (retry_entry["question"], retry_entry["title"], active_conversation()["database_id"])
    context = get_clarification_context(active_conversation()["entries"])
    dictation_target = f"{active_conversation()['id']}:{active_conversation()['database_id']}"
    if st.session_state.get("dictation_target") != dictation_target:
        for key in ("dictation_event", "dictation_draft", "dictation_state", "dictation_error"):
            st.session_state.pop(key, None)
        # Preserve a message just submitted from a second tab after a database
        # change; clear only an unsent frontend draft, never an incoming trigger.
        if st.session_state.get("dictation_target") is not None and not st.session_state.get("sql_input"):
            st.session_state.sql_input = ""
        st.session_state.dictation_target = dictation_target
    with st.bottom:
        with st.container(key="composer"):
            text, microphone = st.columns([10, 1.4], gap="small", vertical_alignment="center")
            # Render the microphone first so its update can prefill the native
            # chat field before Streamlit registers that widget for this rerun.
            with microphone:
                update = validate_dictation_update(
                    render_dictation(dictation_target, disabled=st.session_state.database_archived),
                    dictation_target, st.session_state.get("dictation_event"),
                )
            if update:
                st.session_state.dictation_event = update["event"]
                st.session_state.dictation_state = update["state"]
                st.session_state.dictation_error = update["error"]
                if update["text"] and update["text"] != st.session_state.get("dictation_draft"):
                    st.session_state.sql_input = update["text"]
                    st.session_state.dictation_draft = update["text"]
            listening = st.session_state.get("dictation_state") in {"starting", "listening"}
            prompt = text.chat_input(
                "Odpowiedz na doprecyzowanie…" if context else "Zadaj pytanie po polsku lub angielsku…",
                max_chars=MAX_QUESTION_LENGTH, key="sql_input", disabled=st.session_state.database_archived or listening,
            )
            if st.session_state.get("dictation_error"):
                st.caption(st.session_state.dictation_error)
            elif listening:
                st.caption("Słucham… Tekst pojawia się na bieżąco. Kliknij Stop, popraw tekst i wyślij.")
            else:
                st.caption("Mów → Stop → wyślij. Dyktowanie korzysta z usługi przeglądarki, np. Google.")
    browser_dictation = False
    if prompt:
        example_label = None
        browser_dictation = bool(st.session_state.pop("dictation_draft", None))
        retry_entry = None
        voice_request = None
        submitted = (prompt, "Własna analiza", active_conversation()["database_id"])
    if submitted:
        question, title, database_id = submitted
        use_database(database_id)
        conversation = active_conversation()
        context = (retry_entry.get("clarification_context", []) if retry_entry else
                   get_clarification_context(conversation["entries"]))
        previous = (retry_entry.get("previous_analysis") if retry_entry else
                    get_previous_analysis(conversation["entries"]))
        with st.spinner("Przygotowuję wyniki…" if example_label else "Przygotowuję SQL i analizuję dane…", show_time=True):
            entry = (analyze_example(example_label) if example_label else
                     analyze_question(question, by_id[database_id]["path"], demo=database_id == "demo",
                                      context=context, previous_analysis=previous))
        if retry_entry:
            entry["retry_of"] = retry_entry["id"]
        if voice_request:
            entry["input_method"] = "voice"
            entry["transcription_usage"] = voice_request["transcription_usage"]
        if browser_dictation:
            entry["input_method"] = "voice"
            entry["dictation_provider"] = "browser"
        try:
            entry["id"] = add_analysis(conversation["id"], entry)
        except (OSError, sqlite3.Error):
            # Keep the paid result in this session and never silently repeat it.
            entry["id"] = f"unsaved_{time.time_ns()}"
            entry["unsaved"] = True
            st.session_state.history_notice = "Nie można zapisać wyniku na dysku. Jest dostępny w tej sesji — nie odświeżaj strony."
        if not conversation["entries"]:
            conversation["title"] = title if example_label else entry["title"][:80]
            try:
                save_conversation(conversation)
            except (OSError, sqlite3.Error):
                st.session_state.history_notice = "Nie można zapisać historii. Wynik pozostaje dostępny w tej sesji."
        conversation["entries"].append(entry)

    conversation = active_conversation()
    database = by_id[conversation["database_id"]]
    st.session_state.database_selection = conversation["database_id"]
    selected_dialog = st.session_state.get("active_dialog")

    with st.sidebar:
        st.markdown(
            '<div class="sidebar-brand"><span class="brand-mark">Q</span>SQL Assistant</div>',
            unsafe_allow_html=True,
        )
        st.button(
            "Nowa rozmowa", key="new_chat", icon=":material/add:", type="primary",
            width="stretch", on_click=start_conversation,
        )
        st.caption("ROZMOWY")
        saved = [item for item in st.session_state.conversations if item["entries"]]
        if not saved:
            st.caption("Tutaj pojawią się Twoje rozmowy.")
        for item in reversed(saved):
            title_column, menu_column = st.columns([8, 1.3], gap="small", vertical_alignment="center")
            title_column.button(
                item["title"], key=f"conversation_{item['id']}",
                icon=":material/chat_bubble_outline:", width="stretch",
                type="primary" if item["id"] == conversation["id"] else "secondary",
                on_click=select_conversation, args=(item["id"],),
            )
            with menu_column.popover("⋯", help="Opcje rozmowy", width="stretch"):
                st.caption(by_id[item["database_id"]]["name"])
                st.caption(f"Ostatnia analiza: {display_timestamp(item['entries'][-1]['created_at'])}")
                with st.form(f"rename_conversation_{item['id']}"):
                    name = st.text_input(
                        "Nazwa rozmowy", value=item["title"], max_chars=80, key=f"conversation_name_{item['id']}",
                    )
                    if st.form_submit_button("Zapisz nazwę", key=f"save_chat_name_{item['id']}"):
                        if not name.strip():
                            st.error("Nazwa nie może być pusta.")
                        else:
                            item["title"] = name.strip()
                            save_conversation(item)
                            st.rerun()
                st.caption("Usunięcie rozmowy i jej wyników jest trwałe.")
                st.button(
                    "Usuń rozmowę", key=f"remove_chat_{item['id']}", icon=":material/delete_outline:",
                    on_click=remove_chat, args=(item["id"],),
                )
        with st.container(key="sidebar_footer"):
            st.caption("Rozmowy zapisywane lokalnie")

    with st.container(key="workspace_header"):
        st.markdown(
            '<div class="page-heading">Analiza danych</div>',
            unsafe_allow_html=True,
        )
        with st.container(key="workspace"):
            source, upload, data, ai = st.columns(
                [4, 1.7, 1.7, 1], gap="small", vertical_alignment="bottom",
            )
            labels = {
                item["id"]: item["name"] + (" · usunięta z listy" if item.get("archived") else "")
                for item in available if not item.get("archived") or item["id"] == database["id"]
            }
            source.selectbox(
                "Baza dla tej rozmowy", list(labels), format_func=labels.get,
                key="database_selection", on_change=change_database,
            )
            if upload.button("Bazy", key="open_upload", icon=":material/database:", width="stretch"):
                selected_dialog = "upload"
            if data.button("O danych", key="open_data", icon=":material/table_view:", width="stretch"):
                selected_dialog = "data"
            if ai.button("AI", key="open_ai", icon=":material/key:", width="stretch"):
                selected_dialog = "ai"
        if not api_configured():
            st.caption("Połącz AI przyciskiem powyżej, aby zadawać pytania. Gotowe przykłady działają bez klucza.")
        if database.get("archived"):
            st.info("Oglądasz zapisane wyniki. Przywróć bazę w Bazy → Kosz, aby wykonywać nowe analizy.")
    notice = st.session_state.pop("history_notice", None)
    if notice:
        st.info(notice)
    if not conversation["entries"]:
        welcome_text = (
            "Zadaj pytanie, użyj mikrofonu lub wybierz przykład."
            if database["id"] == "demo" else "Zadaj pytanie o tę bazę lub użyj mikrofonu."
        )
        st.markdown(
            '<div class="welcome">'
            '<h1>Co chcesz przeanalizować?</h1>'
            f'<p>{welcome_text}<br>Bazę i narzędzia masz w panelu powyżej.</p></div>',
            unsafe_allow_html=True,
        )
        if database["id"] == "demo":
            example_buttons()
    else:
        entries = conversation["entries"]
        for index, entry in enumerate(entries):
            render_result(entry, expanded=index == len(entries) - 1)
        if (get_clarification_context(entries) or get_previous_analysis(entries)) and not st.session_state.database_archived:
            st.button(
                "Zacznij nowe pytanie", key="clear_context", icon=":material/add:",
                type="tertiary", on_click=clear_analysis_context,
            )
        if database["id"] == "demo":
            with st.popover("Przykładowe analizy", type="tertiary", icon=":material/lightbulb:"):
                for label in EXAMPLE_QUERIES:
                    st.button(label, key=label, width="stretch", on_click=queue_example, args=(label,))

    st.session_state.active_dialog = selected_dialog
    if selected_dialog == "data":
        show_data(database)
    elif selected_dialog == "upload":
        manage_databases()
    elif selected_dialog == "ai":
        configure_ai()
    elif selected_dialog == "voice":
        voice_input(database)


if __name__ == "__main__":
    main()
