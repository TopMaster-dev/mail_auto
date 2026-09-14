"""
Two faults found once the panel was used in anger (client, 2026-09-11).

1. A row showing 「NG検出」 had NGワード / NGカテゴリ / 判定理由 all blank. Those
   columns are written when the row is first created, which happens *before* any
   check runs, so they were empty for precisely the rows that needed them.

2. Reply detection re-fired on every poll. Since ingestion switched to re-scanning
   a 7-day window, the same reply mail is seen every cycle and was rewriting the
   row and re-logging every few minutes.
"""
import unittest
from unittest.mock import MagicMock

from src.core.inquiry_processor import InquiryProcessor


def _processor():
    p = InquiryProcessor.__new__(InquiryProcessor)
    p._sheets = MagicMock()
    return p


class TestReplyIsActedOnOnce(unittest.TestCase):
    def setUp(self):
        self.p = _processor()
        self.p._sheets.find_inquiry_by_message_id.return_value = "i1"
        self.raw = {"in_reply_to": "<sent-123@rentmagazine.jp>"}

    def test_first_sighting_stops_the_followup(self):
        self.p._sheets.get_inquiry.return_value = {
            "ステータス": "送信済み", "追客ステータス": "追客中"}
        self.p._process_one(self.raw, {})
        self.p._sheets.update_status.assert_called_once_with("i1", "返信あり")
        self.p._sheets.stop_followup.assert_called_once_with("i1")

    def test_second_sighting_changes_nothing(self):
        self.p._sheets.get_inquiry.return_value = {
            "ステータス": "返信あり", "追客ステータス": "追客停止"}
        self.p._process_one(self.raw, {})
        self.p._sheets.update_status.assert_not_called()
        self.p._sheets.stop_followup.assert_not_called()

    def test_a_reply_is_never_treated_as_a_new_inquiry(self):
        """Even when already handled, the mail must not fall through to intake."""
        self.p._sheets.get_inquiry.return_value = {
            "ステータス": "返信あり", "追客ステータス": "追客停止"}
        self.p._process_one(self.raw, {})
        self.p._sheets.write_inquiry.assert_not_called()

    def test_an_unreadable_row_still_stops_the_followup(self):
        """Failing to read must not leave us chasing a customer who replied."""
        self.p._sheets.get_inquiry.side_effect = RuntimeError("boom")
        self.p._process_one(self.raw, {})
        self.p._sheets.stop_followup.assert_called_once_with("i1")

    def test_an_unmatched_reply_falls_through_to_normal_handling(self):
        self.p._sheets.find_inquiry_by_message_id.return_value = None
        self.p._checker = MagicMock()
        # identify() will reject this, so it simply ends up ignored — the point
        # is that Step 1 did not swallow it.
        self.p._process_one({"in_reply_to": "<unknown@x>", "subject": "hi",
                             "body": "hi", "from_addr": "a@b.c"}, {})
        self.p._sheets.update_status.assert_not_called()


class TestNgResultIsWrittenToTheRow(unittest.TestCase):
    def test_sheets_client_writes_all_three_columns(self):
        from src.integrations.sheets_client import (
            SheetsClient, _NG_WORDS_COL, _NG_CATEGORY_COL, _DISC_REASON_COL)
        sc = SheetsClient.__new__(SheetsClient)
        sc.update_inquiry_field = MagicMock()
        sc.update_ng_result("i1", "審査, 保証会社", "審査・保証会社関連", "通常問い合わせ")
        cols = [c[0][1] for c in sc.update_inquiry_field.call_args_list]
        self.assertEqual(cols, [_NG_WORDS_COL, _NG_CATEGORY_COL, _DISC_REASON_COL])
        vals = [c[0][2] for c in sc.update_inquiry_field.call_args_list]
        self.assertEqual(vals, ["審査, 保証会社", "審査・保証会社関連", "通常問い合わせ"])

    def test_the_columns_match_the_header_order(self):
        from src.core.models import Inquiry
        from src.integrations.sheets_client import (
            _NG_WORDS_COL, _NG_CATEGORY_COL, _DISC_REASON_COL)
        h = Inquiry.sheets_headers()
        self.assertEqual(h[_NG_WORDS_COL - 1], "NGワード")
        self.assertEqual(h[_NG_CATEGORY_COL - 1], "NGカテゴリ")
        self.assertEqual(h[_DISC_REASON_COL - 1], "差別表現判定理由")


if __name__ == "__main__":
    unittest.main()
