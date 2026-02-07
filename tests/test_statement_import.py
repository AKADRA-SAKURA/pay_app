import unittest

from app.services.statement_import import (
    detect_duplicates,
    parse_card_csv_preview,
    parse_flexible_date,
    parse_card_text_preview,
)


class StatementImportTests(unittest.TestCase):
    def test_parse_card_text_basic(self) -> None:
        text = """
        2026/02/01 Amazon 1,234円
        2026/02/03 コンビニ -560円
        """
        rows, warnings, errors = parse_card_text_preview(text)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["date"], "2026/02/01")
        self.assertEqual(rows[0]["price"], 1234)
        self.assertIn("Amazon", rows[0]["title"])
        self.assertEqual(rows[1]["price"], -560)

    def test_parse_card_text_broken_lines_and_kind(self) -> None:
        text = """
        2026/02/10
        スーパー
        分割
        2,000円
        """
        rows, warnings, errors = parse_card_text_preview(text)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026/02/10")
        self.assertEqual(rows[0]["price"], 2000)
        self.assertIn("スーパー", rows[0]["title"])
        self.assertIn("分割", rows[0]["title"])
        self.assertEqual(warnings, [])

    def test_parse_card_text_missing_year_requires_user_input(self) -> None:
        text = "02/10 Amazon 1,200円"
        rows, warnings, errors = parse_card_text_preview(text)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "")
        self.assertEqual(rows[0]["date_hint"], "02/10")
        self.assertEqual(rows[0]["price"], 1200)
        self.assertTrue(warnings)

    def test_parse_card_csv_preview(self) -> None:
        csv_bytes = "yyyy/mm/dd,title,price\n2026/02/01,Store A,1000\n".encode("utf-8")
        rows, warnings, errors = parse_card_csv_preview(csv_bytes)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertEqual(rows, [{"date": "2026/02/01", "title": "Store A", "price": 1000}])

    def test_parse_card_csv_appends_payment_kind_tag(self) -> None:
        csv_bytes = "yyyy/mm/dd,title,price\n2026/02/01,Amazon 分割,1000\n".encode("utf-8")
        rows, warnings, errors = parse_card_csv_preview(csv_bytes)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertIn("【分割】", rows[0]["title"])

    def test_detect_duplicates_existing_and_payload(self) -> None:
        rows = [
            {"date": "2026/02/01", "title": "A", "price": 100},
            {"date": "2026/02/01", "title": "A", "price": 100},
            {"date": "2026/02/02", "title": "B", "price": 200},
        ]
        existing = {("2026-02-02", "B", 200, 1)}

        details = detect_duplicates(rows, 1, existing)
        self.assertEqual(len(details), 2)
        self.assertEqual(details[0]["reason"], "payload")
        self.assertEqual(details[1]["reason"], "existing")

    def test_detect_duplicates_skips_invalid_row(self) -> None:
        rows = [
            {"date": "", "title": "A", "price": 100},
            {"date": "2026/02/01", "title": "A", "price": 100},
        ]
        details = detect_duplicates(rows, 1, set())
        self.assertEqual(details, [])

    def test_parse_flexible_date_rejects_mmdd_without_default_year(self) -> None:
        with self.assertRaises(ValueError):
            parse_flexible_date("02/10")

    def test_parse_card_text_no_rows(self) -> None:
        text = "ご利用明細\nページ 1\n"
        rows, warnings, errors = parse_card_text_preview(text)
        self.assertEqual(rows, [])
        self.assertEqual(warnings, [])
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
