"""Bound SQLite results and preserve scalar values without a float detour."""

from __future__ import annotations

from numbers import Integral
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

MAX_RESULT_ROWS = 1000
MAX_RESULT_COLUMNS = 100
MAX_CELL_BYTES = 64 * 1024
MAX_RESULT_BYTES = 4 * 1024 * 1024


class ResultLimitError(ValueError):
    """A result is too large to store or display safely in this application."""


def value_size(value: object) -> int:
    """Count UTF-8 payload bytes, including the displayed form of a BLOB."""
    if isinstance(value, bytes):
        return 2 + 2 * len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    return 8


def check_columns(columns: list[str]) -> int:
    """Validate result width and return the byte budget used by column names."""
    if len(columns) > MAX_RESULT_COLUMNS:
        raise ResultLimitError(f"Wynik ma zbyt wiele kolumn (limit: {MAX_RESULT_COLUMNS}). Wybierz mniej danych.")
    sizes = [value_size(str(column)) for column in columns]
    if any(size > MAX_CELL_BYTES for size in sizes):
        raise ResultLimitError("Nazwa kolumny jest zbyt długa. Użyj krótszego aliasu.")
    return sum(sizes)


def check_row(row, used_bytes: int) -> int:
    """Reject oversized cells and accumulated payloads before building a frame."""
    for value in row:
        size = value_size(value)
        if size > MAX_CELL_BYTES:
            raise ResultLimitError("Komórka wyniku przekracza 64 KB. Pomiń duże teksty lub dane binarne.")
        used_bytes += size
    if used_bytes > MAX_RESULT_BYTES:
        raise ResultLimitError("Wynik przekracza 4 MB. Zawęź zakres analizy.")
    return used_bytes


def result_frame(rows, columns: list[str]) -> pd.DataFrame:
    """Preserve nullable integers and mixed SQLite types column by column."""
    import pandas as pd

    used_bytes = check_columns(columns)
    values = []
    for row in rows:
        if len(values) >= MAX_RESULT_ROWS + 1:
            raise ResultLimitError("Wynik zawiera zbyt wiele wierszy.")
        if len(row) != len(columns):
            raise ValueError("Niezgodna liczba kolumn w wyniku.")
        used_bytes = check_row(row, used_bytes)
        values.append(["0x" + value.hex() if isinstance(value, bytes) else value for value in row])
    series = []
    for index in range(len(columns)):
        cells = [row[index] for row in values]
        present = [value for value in cells if value is not None and value is not pd.NA]
        if present and all(isinstance(value, Integral) and not isinstance(value, bool) for value in present):
            dtype = "Int64" if len(present) != len(cells) else "int64"
        elif present and all(isinstance(value, float) for value in present):
            dtype = "float64"
        else:
            # SQLite can mix strings, floats and integers in one column.
            dtype = "object"
        series.append(pd.Series(cells, dtype=dtype))
    if not series:
        return pd.DataFrame()
    frame = pd.concat(series, axis=1)
    frame.columns = columns
    return frame


def check_frame(frame: pd.DataFrame) -> None:
    """Apply the same limits to restored history before rendering or exporting."""
    if len(frame) > MAX_RESULT_ROWS:
        raise ResultLimitError("Zapisany wynik przekracza limit 1000 wierszy.")
    used_bytes = check_columns(frame.columns.tolist())
    for row in frame.itertuples(index=False, name=None):
        used_bytes = check_row(row, used_bytes)
