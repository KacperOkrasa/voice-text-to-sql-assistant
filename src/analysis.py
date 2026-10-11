"""Coordinate one question, local SQL execution and at most one AI repair."""

import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from src.ai import AIError, generate_sql, previous_analysis_context, repair_sql
from src.db import execute_query, get_schema
from src.results import ResultLimitError, result_frame
from src.security import SQL_INPUT_MESSAGE, UnsafeSQLError, is_sql_input

ROW_LIMIT = 1000


def new_entry(title: str, sql: str = "") -> dict:
    """Build a history entry shared by AI questions and manual SQL."""
    return {"title": title, "sql": sql.strip(),
            "created_at": datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d %H:%M"),
            "frame": pd.DataFrame(), "error": None, "clarification": None,
            "truncated": False, "elapsed": 0.0, "api_usage": []}


def load_result(entry: dict, db_path: str) -> None:
    """Validate and execute SQL locally; never pass the resulting frame to AI."""
    rows, columns = execute_query(entry["sql"], db_path=db_path, row_limit=ROW_LIMIT)
    frame = result_frame(rows, columns)
    entry["truncated"] = len(frame) > ROW_LIMIT
    entry["frame"] = frame.head(ROW_LIMIT)


def repairable_error(error: sqlite3.Error) -> str | None:
    """Map a SQLite error to a generic category, dropping identifiers and values."""
    message = str(error).lower()
    for fragment, kind in (
        ("syntax error", "syntax"), ("incomplete input", "syntax"),
        ("no such column", "unknown_column"), ("no such table", "unknown_table"),
        ("ambiguous column name", "ambiguous_column"),
        ("no such function", "function"), ("wrong number of arguments", "function"),
    ):
        if fragment in message:
            return kind
    return None


def sql_error_message(error: sqlite3.Error) -> str:
    """Describe local failures without exposing paths or raw SQLite error text."""
    if isinstance(error, sqlite3.DataError) or "too many columns" in str(error).lower():
        return "Wynik przekracza limit rozmiaru SQLite. Wybierz mniej lub mniejsze dane."
    if "interrupted" in str(error).lower():
        return "Zapytanie trwało zbyt długo. Spróbuj zawęzić analizę."
    return "Nie udało się wykonać przygotowanego zapytania. Spróbuj doprecyzować pytanie lub zadać je inaczej."


def get_clarification_context(entries: list[dict]) -> list[dict]:
    """Continue only the last unfinished question, never past results or other chats."""
    if not entries:
        return []
    latest = entries[-1]
    if latest.get("context_cancelled") or latest.get("clarification_cancelled"):
        return []
    context = latest.get("clarification_context", [])
    if latest.get("error"):
        # Keep the previous exchange available after a failed API request.
        return context
    if latest.get("clarification") and latest.get("question"):
        return [*context, {"question": latest["question"], "clarification": latest["clarification"]}]
    return []


def get_previous_analysis(entries: list[dict]) -> dict | None:
    """Keep one completed AI analysis in this chat, excluding manual SQL and rows."""
    if not entries:
        return None
    latest = entries[-1]
    if (latest.get("context_cancelled") or latest.get("clarification_cancelled")
            or latest.get("source") != "text" or not latest.get("question")):
        return None
    if latest.get("error") or latest.get("clarification"):
        # Preserve the exact earlier analysis during clarifications and retries.
        return latest.get("previous_analysis")
    if latest.get("sql"):
        return {"question": latest["question"], "sql": latest["sql"]}
    return None


def analyze_question(question: str, db_path: str, demo: bool = False,
                     context: list[dict] | None = None, previous_analysis: dict | None = None) -> dict:
    """Generate once, execute locally, optionally repair once and preserve the question."""
    started = time.perf_counter()
    entry = new_entry(context[0]["question"] if context else question.strip())
    entry["question"], entry["source"] = question.strip(), "text"
    if is_sql_input(question):
        entry.update(source="blocked", error=SQL_INPUT_MESSAGE, error_reason="sql_input")
        entry["elapsed"] = time.perf_counter() - started
        return entry
    if context:
        entry["clarification_context"] = [
            {"question": turn["question"], "clarification": turn["clarification"]} for turn in context
        ]
    try:
        if previous_analysis is not None:
            entry["previous_analysis"] = previous_analysis_context(previous_analysis)
        schema = get_schema(db_path)
        answer = generate_sql(question, schema, demo, context, entry.get("previous_analysis"))
        if answer["usage"] is not None:
            entry["api_usage"].append(answer["usage"])
        entry["clarification"] = answer["clarification"]
        if answer["sql"]:
            entry["sql"] = answer["sql"]
            try:
                load_result(entry, db_path)
            except sqlite3.Error as error:
                kind = repairable_error(error)
                if not kind:
                    raise
                entry["original_sql"] = entry["sql"]
                answer = repair_sql(question, schema, entry["sql"], kind, demo, context,
                                    entry.get("previous_analysis"))
                if answer["usage"] is not None:
                    entry["api_usage"].append(answer["usage"])
                entry["clarification"] = answer["clarification"]
                if answer["sql"]:
                    entry["sql"] = answer["sql"]
                    load_result(entry, db_path)
                    entry["repaired"] = True
    except AIError as error:
        entry["error"] = str(error)
        entry["error_reason"] = error.reason
        if error.reason in {"sql_input", "sql_context"}:
            entry["source"] = "blocked"
            entry.pop("clarification_context", None)
            entry.pop("previous_analysis", None)
        if error.usage is not None:
            entry["api_usage"].append(error.usage)
    except ResultLimitError as error:
        entry["error"] = str(error)
        entry["error_reason"] = "result_limit"
    except UnsafeSQLError as error:
        entry["error"] = str(error)
    except FileNotFoundError:
        entry["error"] = "Plik bazy jest niedostępny. Wgraj go ponownie lub wybierz inną bazę."
    except sqlite3.Error as error:
        entry["error"] = sql_error_message(error)
    entry["elapsed"] = time.perf_counter() - started
    return entry
