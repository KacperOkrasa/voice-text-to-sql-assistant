"""Readable labels and Polish formatting for SQL results."""

import csv
from io import StringIO
from decimal import Decimal
from numbers import Integral, Real
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
from pandas.io.formats.style import Styler

from src.results import check_frame


COLUMN_LABELS = {
    "miesiac": "Miesiąc", "month": "Miesiąc",
    "przychod_pln": "Przychód [zł]", "revenue_pln": "Przychód [zł]",
    "produkt": "Produkt", "name": "Nazwa", "category": "Kategoria",
    "sprzedane_sztuki": "Sprzedane sztuki", "quantity": "Liczba sztuk",
    "miasto": "Miasto", "city": "Miasto", "status": "Status zamówienia",
    "liczba_zamowien": "Liczba zamówień", "order_count": "Liczba zamówień",
    "customer_count": "Liczba klientów", "customers": "Liczba klientów",
    "full_name": "Imię i nazwisko", "email": "E-mail",
    "order_date": "Data zamówienia", "registered_at": "Data rejestracji",
    "price_cents": "Cena [zł]", "unit_price_cents": "Cena za sztukę [zł]",
    "customer_id": "ID klienta", "product_id": "ID produktu",
    "order_id": "ID zamówienia", "order_item_id": "ID pozycji",
}
STATUS_LABELS = {
    "paid": "Opłacone", "shipped": "Wysłane",
    "delivered": "Dostarczone", "cancelled": "Anulowane",
}
MONTH_NAMES = (
    "styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec",
    "lipiec", "sierpień", "wrzesień", "październik", "listopad", "grudzień",
)


def display_timestamp(value: str) -> str:
    """Display saved timestamps in Polish time, including older local timestamps."""
    if not value:
        return "Data nieznana (starszy zapis)"
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        try:
            return datetime.strptime(value, "%H:%M").strftime("%H:%M") + " (starszy zapis, bez daty)"
        except ValueError:
            return "Data nieznana (starszy zapis)"
    if timestamp.tzinfo is not None:
        timestamp = timestamp.astimezone(ZoneInfo("Europe/Warsaw"))
    return timestamp.strftime("%d.%m.%Y, %H:%M")


def column_label(column: str) -> str:
    """Translate known columns and make custom SQL aliases readable."""
    name = str(column)
    if name.lower() in COLUMN_LABELS:
        return COLUMN_LABELS[name.lower()]
    if name.lower().endswith(("_pln", "_cents")):
        name = name.rsplit("_", 1)[0] + " [zł]"
    return name.replace("_", " ").capitalize()


def is_money_column(column: str) -> bool:
    """Recognize explicit currency aliases rather than guessing unknown units."""
    return str(column).lower().endswith(("_pln", "_cents"))


def format_value(value: object, column: str = "") -> str:
    """Format a scalar without modifying the underlying value used for sorting."""
    if pd.isna(value):
        return "—"
    if isinstance(value, Integral) and not isinstance(value, bool) and not is_money_column(column):
        return f"{value:,d}".replace(",", " ")
    if isinstance(value, (Real, Decimal)) and not isinstance(value, bool):
        number = Decimal(str(value))
        if not number.is_finite():
            return "∞" if number > 0 else "−∞"
        money = is_money_column(column)
        if str(column).lower().endswith("_cents"):
            number /= 100
        decimals = 2 if money or number != number.to_integral_value() else 0
        formatted = f"{number:,.{decimals}f}".replace(",", " ").replace(".", ",")
        return formatted + " zł" if money else formatted
    if str(column).lower() == "status":
        return STATUS_LABELS.get(str(value), str(value))
    if str(column).lower() in ("month", "miesiac"):
        text = str(value)
        if len(text) == 7 and text[4] == "-" and text[:4].isdigit() and text[5:].isdigit():
            month = int(text[5:])
            if 1 <= month <= 12:
                return f"{MONTH_NAMES[month - 1]} {text[:4]}"
    return str(value)


def display_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Build a labelled view whose numeric values match the displayed units."""
    check_frame(frame)
    display = frame.copy()
    labels = []
    for index, column in enumerate(frame.columns):
        base = column_label(column)
        label = base
        suffix = 2
        while label in labels:
            label = f"{base} ({suffix})"
            suffix += 1
        labels.append(label)
        if str(column).lower().endswith("_cents"):
            display.isetitem(index, pd.Series([
                Decimal(str(value)) / 100 if isinstance(value, (Real, Decimal)) and not pd.isna(value)
                else value for value in frame.iloc[:, index]
            ], index=frame.index, dtype=object))
    display.columns = labels
    return display


def browser_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Avoid JavaScript rounding and Arrow coercion of mixed SQLite columns."""
    check_frame(frame)
    display = frame.copy()
    for index in range(len(frame.columns)):
        values = frame.iloc[:, index].tolist()
        present = [value for value in values if not pd.isna(value)]
        kinds = {"number" if isinstance(value, (Real, Decimal)) else type(value).__name__ for value in present}
        large_integer = any(isinstance(value, Integral) and abs(value) > 2**53 - 1 for value in present)
        mixed_numbers = any(isinstance(value, Integral) for value in present) and any(isinstance(value, float) for value in present)
        precise_decimal = any(isinstance(value, Decimal) and
                              (not value.is_finite() or len(value.as_tuple().digits) > 15) for value in present)
        if large_integer or precise_decimal or len(kinds) > 1 or mixed_numbers:
            display.isetitem(index, pd.Series([
                None if pd.isna(value) else str(value) for value in values
            ], index=frame.index, dtype=object))
    return display


def style_table(frame: pd.DataFrame) -> Styler:
    """Format a display copy, including an actual cents-to-PLN conversion."""
    display = browser_frame(display_frame(frame))
    formatters = {}
    for source, label in zip(frame.columns, display.columns):
        unit = str(source)
        if unit.lower().endswith("_cents"):
            unit = unit[:-6] + "_pln"
        formatters[label] = lambda value, column=unit: format_value(value, column)
    return display.style.format(formatters, na_rep="—")


def spreadsheet_cell(value: object) -> object:
    """Keep imported text inert in spreadsheets and preserve long identifiers."""
    if isinstance(value, str):
        if value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        digits = value.lstrip("+-")
        if digits.isdigit() and (len(digits) > 15 or (len(digits) > 1 and digits.startswith("0"))):
            return "'" + value
    if isinstance(value, Integral) and abs(value) >= 10**15:
        return "'" + str(value)
    if isinstance(value, Decimal) and (not value.is_finite() or len(value.as_tuple().digits) > 15):
        return "'" + str(value)
    return value


def export_csv(frame: pd.DataFrame, spreadsheet: bool = True) -> bytes:
    """Export either labelled spreadsheet data or unchanged raw scalar values."""
    check_frame(frame)
    exported = display_frame(frame) if spreadsheet else frame
    buffer = StringIO(newline="")
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL)
    protect = spreadsheet_cell if spreadsheet else lambda value: value
    writer.writerow([protect(str(column)) for column in exported.columns])
    for row in exported.itertuples(index=False, name=None):
        writer.writerow(["" if pd.isna(value) else protect(value) for value in row])
    return buffer.getvalue().encode("utf-8-sig")
