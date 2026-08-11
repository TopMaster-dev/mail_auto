"""Unit tests for Inquiry.from_sheets_record (used by scheduler + admin)."""
import unittest
from datetime import datetime

from src.core.models import Inquiry


class TestFromSheetsRecord(unittest.TestCase):
    def _rec(self, **over):
        base = {
            "ID": "id-1", "受信日時": "2026-06-30 10:00:00", "顧客名": "山田 太郎",
            "メールアドレス": "taro@example.com", "問い合わせ物件": "物件A",
            "物件URL": "https://rentmagazine.jp/estate/a", "空室有無": "あり",
            "ステータス": "送信済み", "AI返信文案": "文案テキスト",
            "追客ステータス": "追客中", "次回追客予定日": "2026-06-28 09:30:00",
            "送信日時": "2026-06-26 11:00:00", "追客回数": "1", "担当者メモ": "メモ",
        }
        base.update(over)
        return base

    def test_basic_fields(self):
        inq = Inquiry.from_sheets_record(self._rec())
        self.assertEqual(inq.id, "id-1")
        self.assertEqual(inq.customer_name, "山田 太郎")
        self.assertEqual(inq.customer_email, "taro@example.com")
        self.assertEqual(inq.inquiry_property_name, "物件A")
        self.assertEqual(inq.followup_count, 1)
        self.assertEqual(inq.followup_status, "追客中")

    def test_vacancy_parsing(self):
        self.assertIs(Inquiry.from_sheets_record(self._rec(空室有無="あり")).is_vacant, True)
        self.assertIs(Inquiry.from_sheets_record(self._rec(空室有無="なし")).is_vacant, False)
        self.assertIsNone(Inquiry.from_sheets_record(self._rec(空室有無="不明")).is_vacant)

    def test_dates_parsed(self):
        inq = Inquiry.from_sheets_record(self._rec())
        self.assertIsInstance(inq.next_followup_at, datetime)
        self.assertIsInstance(inq.sent_at, datetime)

    def test_blank_count_defaults_zero(self):
        self.assertEqual(Inquiry.from_sheets_record(self._rec(追客回数="")).followup_count, 0)

    def test_float_formatted_count_is_preserved(self):
        # Sheets hands back "1.0" for a numeric cell; int("1.0") raises, and the
        # old fallback reset the counter to 0 — re-sending an earlier follow-up.
        self.assertEqual(
            Inquiry.from_sheets_record(self._rec(追客回数="1.0")).followup_count, 1)

    def test_unparseable_count_falls_back_to_zero(self):
        self.assertEqual(
            Inquiry.from_sheets_record(self._rec(追客回数="なし")).followup_count, 0)

    def test_missing_dates_are_none(self):
        inq = Inquiry.from_sheets_record(self._rec(次回追客予定日="", 送信日時=""))
        self.assertIsNone(inq.next_followup_at)
        self.assertIsNone(inq.sent_at)


if __name__ == "__main__":
    unittest.main()
