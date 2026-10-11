"""Read-only SQLite connections and schema retrieval for the analytics app."""

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from src.security import authorize_read, validate_sql
from src.results import MAX_RESULT_ROWS, check_columns, check_row

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "ecommerce.db"


def get_connection(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open an existing database for reading; the caller must close it.

    Read-only mode protects the database even if SQL validation is accidentally
    skipped. query_only also blocks writes to temporary tables on this connection.
    """
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(
            f"Nie znaleziono bazy: {path}. Utwórz ją poleceniem: python -m data.seed"
        )

    # as_uri() safely escapes spaces and characters such as ? or # in the path.
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    # Bound individual SQLite values/rows before Python receives them.
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024 * 1024)
    connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 512)
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA trusted_schema = OFF")
    return connection


def get_schema(db_path: str | Path = DEFAULT_DB_PATH) -> str:
    """Return only structural metadata for an LLM, never records or raw DDL.

    DEFAULT values, CHECK expressions and view definitions may contain sensitive
    literals, so they are deliberately excluded. Names and declared types are
    metadata and can still reveal information about the database's purpose.
    """
    schema = []
    with closing(get_connection(db_path)) as connection:
        rows = connection.execute(
            "SELECT name, type FROM sqlite_master "
            "WHERE type IN ('table', 'view') AND name NOT GLOB 'sqlite_*' ORDER BY name"
        ).fetchall()
        for table in rows:
            identifier = '"' + table["name"].replace('"', '""') + '"'
            columns = [
                {"name": row["name"], "type": row["type"], "primary_key": bool(row["pk"]),
                 "not_null": bool(row["notnull"])}
                for row in connection.execute(f"PRAGMA table_info({identifier})")
            ]
            foreign_keys = [
                {"column": row["from"], "reference_table": row["table"], "reference_column": row["to"]}
                for row in connection.execute(f"PRAGMA foreign_key_list({identifier})")
            ]
            schema.append({"name": table["name"], "kind": table["type"],
                           "columns": columns, "foreign_keys": foreign_keys})
    return json.dumps({"tables": schema}, ensure_ascii=False, indent=2)


def get_database_summary(db_path: str | Path) -> dict:
    """Read file size and table/view counts without loading database records."""
    with closing(get_connection(db_path)) as connection:
        counts = dict(connection.execute(
            "SELECT type, COUNT(*) FROM sqlite_master "
            "WHERE type IN ('table', 'view') AND name NOT GLOB 'sqlite_*' GROUP BY type"
        ))
    return {"tables": counts.get("table", 0), "views": counts.get("view", 0),
            "size_bytes": Path(db_path).stat().st_size}


def get_relationships(db_path: str | Path = DEFAULT_DB_PATH) -> list[dict]:
    """Describe all foreign keys as a table suitable for the data browser."""
    relationships = []
    for table in json.loads(get_schema(db_path))["tables"]:
        for key in table["foreign_keys"]:
            relationships.append({
                "Tabela": table["name"], "Kolumna": key["column"],
                "Tabela docelowa": key["reference_table"],
                "Kolumna docelowa": key["reference_column"] or "(klucz główny)",
            })
    return relationships


def get_tables(db_path: str | Path = DEFAULT_DB_PATH) -> list[str]:
    """List user tables and views, including names with spaces or quotes."""
    with closing(get_connection(db_path)) as connection:
        return [row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
            "AND name NOT GLOB 'sqlite_*' ORDER BY name"
        )]


def table_identifier(table: str, db_path: str | Path) -> str:
    """Accept only an existing table/view name and quote it as an identifier."""
    if table not in get_tables(db_path):
        raise ValueError("Nie znaleziono wybranej tabeli w bazie.")
    return '"' + table.replace('"', '""') + '"'


def get_table_columns(table: str, db_path: str | Path = DEFAULT_DB_PATH) -> list[dict]:
    """Return readable metadata rather than CREATE TABLE source code."""
    identifier = table_identifier(table, db_path)
    with closing(get_connection(db_path)) as connection:
        return [
            {
                "Kolumna": row["name"], "Typ": row["type"] or "—",
                "Klucz główny": "Tak" if row["pk"] else "Nie",
                "NOT NULL": "Tak" if row["notnull"] else "Nie",
            }
            for row in connection.execute(f"PRAGMA table_info({identifier})")
        ]


def get_table_page(
    table: str, db_path: str | Path = DEFAULT_DB_PATH, page: int = 1, page_size: int = 50,
) -> tuple[list[sqlite3.Row], list[str], int]:
    """Return a page and total row count, with the same time budget as analyses."""
    if page < 1 or not 1 <= page_size <= 1000:
        raise ValueError("Nieprawidłowy numer strony lub liczba wierszy.")
    identifier = table_identifier(table, db_path)
    deadline = time.monotonic() + 5.0
    with closing(get_connection(db_path)) as connection:
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        count = connection.execute(f"SELECT COUNT(*) FROM {identifier}").fetchone()[0]
        # Column order provides stable paging for an unchanged local database.
        names = [row["name"] for row in connection.execute(f"PRAGMA table_info({identifier})")]
        order = ", ".join('"' + name.replace('"', '""') + '"' for name in names)
        cursor = connection.execute(
            f"SELECT * FROM {identifier} ORDER BY {order} LIMIT ? OFFSET ?",
            (page_size, (page - 1) * page_size),
        )
        rows, columns = fetch_result(cursor, page_size)
    return rows, columns, count


def execute_query(
    sql: str,
    db_path: str | Path = DEFAULT_DB_PATH,
    row_limit: int = 1000,
    timeout_seconds: float = 5.0,
) -> tuple[list[sqlite3.Row], list[str]]:
    """Validate and execute a SELECT with a time budget and bounded result size.

    Fetch at most row_limit + 1 rows so the UI can detect a truncated result.
    Return rows and column names; SQLite errors propagate to the caller.
    """
    query = validate_sql(sql)
    if not 1 <= row_limit <= MAX_RESULT_ROWS or timeout_seconds <= 0:
        raise ValueError("Limit wierszy i czas wykonania muszą być dodatnie.")

    deadline = time.monotonic() + timeout_seconds
    with closing(get_connection(db_path)) as connection:
        connection.set_authorizer(authorize_read)
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        cursor = connection.execute(query)
        rows, columns = fetch_result(cursor, row_limit + 1)
    return rows, columns


def fetch_result(cursor: sqlite3.Cursor, row_limit: int) -> tuple[list, list[str]]:
    """Fetch incrementally, bounding columns, cells and total payload bytes."""
    columns = [column[0] for column in cursor.description]
    used_bytes = check_columns(columns)
    rows = []
    for _ in range(row_limit):
        row = cursor.fetchone()
        if row is None:
            break
        used_bytes = check_row(row, used_bytes)
        rows.append(row)
    return rows, columns


def validate_database(db_path: str | Path, timeout_seconds: float = 5.0) -> None:
    """Check integrity and prepare every table/view within one time budget."""
    deadline = time.monotonic() + timeout_seconds
    with closing(get_connection(db_path)) as connection:
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        if [row[0] for row in connection.execute("PRAGMA quick_check(1)")] != ["ok"]:
            raise ValueError("Baza jest uszkodzona. Wgraj poprawny plik SQLite.")
        objects = connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
            "AND name NOT GLOB 'sqlite_*' LIMIT 1001"
        ).fetchall()
        if not objects:
            raise ValueError("Baza nie zawiera tabel ani widoków do analizy.")
        if len(objects) > 1000:
            raise ValueError("Baza zawiera zbyt wiele tabel i widoków (limit: 1000).")
        connection.set_authorizer(authorize_read)
        for row in objects:
            if time.monotonic() > deadline:
                raise ValueError("Sprawdzanie bazy trwało zbyt długo. Wgraj mniejszą bazę.")
            identifier = '"' + row[0].replace('"', '""') + '"'
            cursor = connection.execute(f"SELECT * FROM {identifier} LIMIT 0")
            check_columns([column[0] for column in cursor.description])
