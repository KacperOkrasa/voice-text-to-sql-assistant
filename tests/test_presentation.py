"""Verify displayed units, numeric sorting and ambiguous result columns."""

import unittest
import csv
import io
from decimal import Decimal

import pandas as pd

from src.presentation import display_timestamp, export_csv, format_value, style_table


class PresentationTests(unittest.TestCase):
    def test_timestamps_support_old_history_and_polish_timezone(self) -> None:
        self.assertEqual(display_timestamp("2026-10-04T18:00:00+00:00"), "04.10.2026, 20:00")
        self.assertEqual(display_timestamp("2026-10-04 16:47"), "04.10.2026, 16:47")
        self.assertEqual(display_timestamp("16:47"), "16:47 (starszy zapis, bez daty)")
        self.assertIn("Data nieznana", display_timestamp("invalid"))
        self.assertIn("Data nieznana", display_timestamp(""))

    def test_money_and_null_values(self) -> None:
        self.assertEqual(format_value(2989.45, "przychod_pln"), "2 989,45 zł")
        self.assertEqual(format_value(24990, "price_cents"), "249,90 zł")
        self.assertEqual(format_value(None, "price_cents"), "—")
        self.assertEqual(format_value(9223372036854775807, "id"), "9 223 372 036 854 775 807")

    def test_table_units_match_underlying_display_numbers_without_mutating_source(self) -> None:
        frame = pd.DataFrame({"name": ["A", "B"], "price_cents": [24990, 990]})
        original = frame.copy()
        styled = style_table(frame)
        self.assertEqual(styled.data["Cena [zł]"].tolist(), [Decimal("249.90"), Decimal("9.90")])
        self.assertIn("249,90 zł", styled.to_html())
        pd.testing.assert_frame_equal(frame, original)

    def test_duplicate_labels_remain_displayable(self) -> None:
        frame = pd.DataFrame([[1, 2]], columns=["quantity", "quantity"])
        styled = style_table(frame)
        self.assertEqual(styled.data.columns.tolist(), ["Liczba sztuk", "Liczba sztuk (2)"])

    def test_polish_months_and_statuses(self) -> None:
        self.assertEqual(format_value("2025-03", "month"), "marzec 2025")
        self.assertEqual(format_value("2025-13", "month"), "2025-13")
        self.assertEqual(format_value("delivered", "status"), "Dostarczone")

    def test_spreadsheet_csv_matches_money_units_but_raw_preserves_cents(self):
        frame = pd.DataFrame({"price_cents": [24990, -990], "note": ["=1+1", "normal"]})
        sheet = list(csv.reader(io.StringIO(export_csv(frame).decode("utf-8-sig"))))
        raw = list(csv.reader(io.StringIO(export_csv(frame, False).decode("utf-8-sig"))))
        self.assertEqual(sheet[0][0], "Cena [zł]")
        self.assertEqual(Decimal(sheet[1][0]), Decimal("249.90"))
        self.assertEqual(Decimal(sheet[2][0]), Decimal("-9.90"))
        self.assertEqual(sheet[1][1], "'=1+1")
        self.assertEqual(raw[0][0], "price_cents")
        self.assertEqual(raw[1], ["24990", "=1+1"])

    def test_spreadsheet_protects_headers_and_text_including_whitespace(self):
        texts = ["=1+1", "+SUM(A1)", "-1+2", "@SUM(A1)", "  =1+1", "\t=1+1", "\rtext", "\ntext",
                 'text, with "quotes"\nand newline', "normal"]
        frame = pd.DataFrame({"=header": texts})
        sheet = list(csv.reader(io.StringIO(export_csv(frame).decode("utf-8-sig"))))
        self.assertEqual(sheet[0][0], "'=header")
        self.assertEqual([row[0] for row in sheet[1:9]], ["'" + text for text in texts[:8]])
        self.assertEqual([row[0] for row in sheet[9:]], texts[8:])


    def test_spreadsheet_preserves_sixteen_digit_ids_and_leading_zero_codes(self):
        frame = pd.DataFrame({"id": [1000000000000001], "code": ["00123"]})
        sheet = list(csv.reader(io.StringIO(export_csv(frame).decode("utf-8-sig"))))
        self.assertEqual(sheet[1], ["'1000000000000001", "'00123"])
        raw = list(csv.reader(io.StringIO(export_csv(frame, False).decode("utf-8-sig"))))
        self.assertEqual(raw[1], ["1000000000000001", "00123"])



if __name__ == "__main__":
    unittest.main()
