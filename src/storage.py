"""Persist local conversations, result snapshots and imported database metadata."""

import json
import math
from numbers import Integral, Real
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

import pandas as pd

from src.db import PROJECT_ROOT
from src.results import check_frame, result_frame

DEFAULT_STORAGE_PATH = PROJECT_ROOT / "data" / "app_history.db"


def get_storage_connection() -> sqlite3.Connection:
    """Open the app's writable storage, separate from analytical databases."""
    DEFAULT_STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DEFAULT_STORAGE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            database_id TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id),
            payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS databases (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            path TEXT NOT NULL,
            imported_at TEXT NOT NULL DEFAULT '',
            archived INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    # Add fields to existing local installations without replacing their data.
    for table, fields in (
        ("databases", {"imported_at": "TEXT NOT NULL DEFAULT ''", "archived": "INTEGER NOT NULL DEFAULT 0"}),
    ):
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        for name, declaration in fields.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
    connection.commit()
    return connection


def encode_cell(value: object) -> object:
    """Use JSON numbers without Pandas rounding; tag infinities explicitly."""
    if value is None or value is pd.NA or pd.isna(value):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        number = float(value)
        return number if math.isfinite(number) else {"float": "inf" if number > 0 else "-inf"}
    raise ValueError("Nieobsługiwany typ wartości w historii.")


def encode_entry(entry: dict) -> str:
    """Store versioned scalar rows with exact integers and float round-trips."""
    if entry.get("source") == "corrupt":
        raise ValueError("Uszkodzony wpis pozostaje zachowany bez zmian.")
    frame = entry["frame"]
    check_frame(frame)
    payload = {key: value for key, value in entry.items() if key != "frame"}
    payload["format_version"] = 2
    payload["frame"] = {"columns": frame.columns.tolist(), "data": [
        [encode_cell(value) for value in row] for row in frame.itertuples(index=False, name=None)
    ]}
    payload["dtypes"] = [str(dtype) for dtype in frame.dtypes]
    return json.dumps(payload, ensure_ascii=False, allow_nan=False)


def validate_entry_metadata(entry: dict) -> None:
    """Check the small set of metadata fields consumed by UI and AI context."""
    for name in ("title", "sql", "created_at", "question", "source", "error", "clarification"):
        optional = name in ("question", "source", "error", "clarification")
        if name in entry and not (optional and entry[name] is None) and not isinstance(entry[name], str):
            raise ValueError("Nieprawidłowe metadane historii.")
    elapsed = entry.get("elapsed", 0)
    if not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError("Nieprawidłowy czas analizy.")
    for field in ("api_usage", "transcription_usage"):
        usage = entry.get(field, [])
        if not isinstance(usage, list):
            raise ValueError("Nieprawidłowe metadane zużycia.")
        for item in usage:
            if not isinstance(item, dict):
                raise ValueError("Nieprawidłowe metadane zużycia.")
            keys = ("seconds",) if field == "transcription_usage" else ("input_tokens", "output_tokens")
            for key in (*keys, "estimated_usd"):
                value = item[key]
                if value is None and key == "estimated_usd" and field == "api_usage":
                    continue
                if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise ValueError("Nieprawidłowe metadane zużycia.")
            if field == "api_usage" and not isinstance(item["model"], str):
                raise ValueError("Nieprawidłowa nazwa modelu.")
    previous = entry.get("previous_analysis")
    if previous is not None and (not isinstance(previous, dict) or
            any(not isinstance(previous.get(key), str) for key in ("question", "sql"))):
        raise ValueError("Nieprawidłowy kontekst historii.")
    context = entry.get("clarification_context", [])
    if not isinstance(context, list) or any(not isinstance(turn, dict) or
            any(not isinstance(turn.get(key), str) for key in ("question", "clarification")) for turn in context):
        raise ValueError("Nieprawidłowy kontekst doprecyzowania.")


def decode_entry(payload: str, analysis_id: int) -> dict:
    """Read v2 or legacy snapshots; isolate damage without rewriting stored data."""
    try:
        entry = json.loads(payload)
        if not isinstance(entry, dict) or entry.get("format_version", 1) not in (1, 2):
            raise ValueError("Nieznany format historii.")
        validate_entry_metadata(entry)
        snapshot = entry["frame"]
        if entry.get("format_version", 1) == 1:
            snapshot = json.loads(snapshot)
        columns, rows = snapshot["columns"], snapshot["data"]
        dtypes = entry.pop("dtypes")
        allowed = {"object", "str", "string", "int64", "Int64", "float64", "Float64", "bool", "boolean"}
        if (not isinstance(columns, list) or not all(isinstance(name, str) for name in columns)
                or not isinstance(rows, list) or not isinstance(dtypes, list)
                or len(dtypes) != len(columns) or any(dtype not in allowed for dtype in dtypes)):
            raise ValueError("Nieprawidłowa struktura wyniku.")
        decoded_rows = []
        for row in rows:
            if not isinstance(row, list) or len(row) != len(columns):
                raise ValueError("Nieprawidłowy wiersz historii.")
            cells = []
            for value in row:
                if isinstance(value, dict) and value in ({"float": "inf"}, {"float": "-inf"}):
                    value = float(value["float"])
                elif value is not None and not isinstance(value, (str, int, float, bool)):
                    raise ValueError("Nieprawidłowa komórka historii.")
                cells.append(value)
            decoded_rows.append(cells)
        # Validate payload limits and construct without a lossy inferred float column.
        frame = result_frame(decoded_rows, columns)
        check_frame(frame)
        if columns:
            frame = pd.concat([
                pd.Series([row[index] for row in decoded_rows], dtype=dtype)
                for index, dtype in enumerate(dtypes)
            ], axis=1)
            frame.columns = columns
        defaults = {"title": "Zapisana analiza", "sql": "", "created_at": "", "error": None,
                    "truncated": False, "elapsed": 0.0, "api_usage": []}
        entry = defaults | entry
        entry["frame"], entry["id"] = frame, analysis_id
        return entry
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError):
        return {"id": analysis_id, "title": "Nieczytelny zapis", "sql": "", "created_at": "",
                "frame": pd.DataFrame(), "source": "corrupt", "truncated": False, "elapsed": 0,
                "error": "Nie można odczytać tego wyniku. Pozostałe rozmowy są dostępne. "
                         "Oryginalny zapis zachowano bez zmian.", "api_usage": []}


def create_conversation(database_id: str = "demo") -> dict:
    """Allocate a persistent conversation ID, also safe for multiple tabs."""
    with closing(get_storage_connection()) as connection, connection:
        cursor = connection.execute(
            "INSERT INTO conversations (title, database_id) VALUES (?, ?)",
            ("Nowa rozmowa", database_id),
        )
    return {
        "id": cursor.lastrowid, "title": "Nowa rozmowa", "database_id": database_id,
        "entries": [],
    }


def load_conversations() -> list[dict]:
    """Load all local conversations and their stored results."""
    with closing(get_storage_connection()) as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(conversations)")}
        # Old trash entries stay hidden; current deletions remove the rows entirely.
        legacy_filter = "WHERE archived = 0" if "archived" in columns else ""
        conversations = [dict(row, entries=[]) for row in connection.execute(
            f"SELECT id, title, database_id FROM conversations {legacy_filter} ORDER BY id"
        )]
        by_id = {item["id"]: item for item in conversations}
        for row in connection.execute("SELECT * FROM analyses ORDER BY id"):
            if row["conversation_id"] in by_id:
                by_id[row["conversation_id"]]["entries"].append(decode_entry(row["payload"], row["id"]))
    return conversations


def load_conversation(conversation_id: int) -> dict | None:
    """Refresh one chat from disk without loading every other result snapshot."""
    with closing(get_storage_connection()) as connection:
        # Read metadata and messages from the same SQLite snapshot.
        connection.execute("BEGIN")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(conversations)")}
        legacy_filter = "AND archived = 0" if "archived" in columns else ""
        row = connection.execute(
            f"SELECT id, title, database_id FROM conversations WHERE id = ? {legacy_filter}",
            (conversation_id,),
        ).fetchone()
        if row is None:
            return None
        conversation = dict(row, entries=[])
        for entry in connection.execute(
            "SELECT id, payload FROM analyses WHERE conversation_id = ? ORDER BY id", (conversation_id,),
        ):
            conversation["entries"].append(decode_entry(entry["payload"], entry["id"]))
    return conversation


def save_conversation(conversation: dict) -> None:
    """Save its title and assigned database without overwriting other results."""
    with closing(get_storage_connection()) as connection, connection:
        connection.execute(
            "UPDATE conversations SET title = ?, database_id = ? WHERE id = ?",
            (conversation["title"], conversation["database_id"], conversation["id"]),
        )


def delete_conversation(conversation_id: int) -> None:
    """Permanently remove a conversation, its snapshots and its saved selection."""
    with closing(get_storage_connection()) as connection, connection:
        connection.execute("DELETE FROM analyses WHERE conversation_id = ?", (conversation_id,))
        connection.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        connection.execute(
            "DELETE FROM settings WHERE key = 'active_conversation' AND value = ?", (str(conversation_id),),
        )


def conversation_exists(conversation_id: int) -> bool:
    """Check a cached chat before using it, including when another tab deleted it."""
    with closing(get_storage_connection()) as connection:
        return connection.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone() is not None


def save_active_conversation(conversation_id: int) -> None:
    """Remember the last opened conversation for the local installation."""
    with closing(get_storage_connection()) as connection, connection:
        connection.execute(
            "INSERT INTO settings (key, value) VALUES ('active_conversation', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(conversation_id),),
        )


def load_active_conversation() -> int | None:
    """Return the remembered ID; the UI checks that the conversation still exists."""
    with closing(get_storage_connection()) as connection:
        row = connection.execute("SELECT value FROM settings WHERE key = 'active_conversation'").fetchone()
    try:
        return int(row["value"]) if row else None
    except (ValueError, TypeError):
        return None


def add_analysis(conversation_id: int, entry: dict) -> int:
    """Append one result atomically and return its persistent ID."""
    with closing(get_storage_connection()) as connection, connection:
        cursor = connection.execute(
            "INSERT INTO analyses (conversation_id, payload) VALUES (?, ?)",
            (conversation_id, encode_entry(entry)),
        )
    return cursor.lastrowid


def save_analysis(entry: dict) -> None:
    """Update entry metadata along with its existing result snapshot."""
    with closing(get_storage_connection()) as connection, connection:
        connection.execute("UPDATE analyses SET payload = ? WHERE id = ?", (encode_entry(entry), entry["id"]))


def register_database(database: dict) -> None:
    """Keep an imported database available after a restart."""
    with closing(get_storage_connection()) as connection, connection:
        connection.execute(
            "INSERT INTO databases (id, name, path, imported_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET path = excluded.path, archived = 0",
            (database["id"], database["name"], database["path"], datetime.now(timezone.utc).isoformat()),
        )


def list_imported_databases(include_archived: bool = False) -> list[dict]:
    """Return locally registered SQLite files, including missing ones for history."""
    with closing(get_storage_connection()) as connection:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM databases WHERE archived = 0 OR ? ORDER BY rowid", (include_archived,),
        )]


def rename_database(database_id: str, name: str) -> None:
    """Change the display name, leaving the copied SQLite file untouched."""
    name = name.strip()
    if not name or len(name) > 80:
        raise ValueError("Nazwa bazy musi mieć od 1 do 80 znaków.")
    with closing(get_storage_connection()) as connection, connection:
        connection.execute("UPDATE databases SET name = ? WHERE id = ?", (name, database_id))


def archive_database(database_id: str, archived: bool = True) -> None:
    """Hide a database from the list while keeping its file and conversation history."""
    with closing(get_storage_connection()) as connection, connection:
        connection.execute("UPDATE databases SET archived = ? WHERE id = ?", (archived, database_id))
