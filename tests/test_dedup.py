"""
The same mail must not produce two inquiry rows.

Mail is normally consumed once — the IMAP fetch marks it seen — but anything
that returns it to unread (an operator re-testing, a flag that fails to stick)
used to create a second row. Two rows at 自動送信可 means the customer can be
sent the identical reply twice.
"""
import unittest
from unittest.mock import MagicMock

from src.core.inquiry_processor import InquiryProcessor
from src.core.models import Inquiry

SUUMO_BODY = """\
問合せ物件
物件名:オリーブ201
最寄り駅:名鉄西尾線/南安城
所在地:愛知県安城市東新町
賃料:6.3万円
間取り:1LDK
専有面積:40.32平米
お客様プロフィール
名前(漢字):三宅しのぶ
メールアドレス:shinobu@example.com
お問合せ内容:この部屋を実際に見学したい
"""


def _raw(message_id):
    return {"from_addr": "system@jds.suumo.jp",
            "subject": "[リクルートJDS]反響お知らせメール",
            "body": SUUMO_BODY, "message_id": message_id,
            "in_reply_to": "", "date": None, "uid": "1"}


def _processor():
    sheets = MagicMock()
    sheets.find_inquiry_by_message_id.return_value = None
    wp = MagicMock()
    wp.get_property_by_url.return_value = None
    wp.resolve_by_formal_name.return_value = (None, "")
    wp.properties = []
    checker = MagicMock()
    checker.check.return_value = MagicMock(is_clean=False, ng_hits=[],
                                           discriminatory=False,
                                           discriminatory_reason="停止",
                                           complaint=False, complaint_reason="")
    proc = InquiryProcessor(
        gmail=MagicMock(), sheets=sheets, wp=wp, checker=checker,
        generator=MagicMock(), scorer=MagicMock(), gate=MagicMock(),
        company={"staff_name": "担当"}, followup_cfg={})
    return proc, sheets


class TestMessageIdDedup(unittest.TestCase):
    def test_unseen_mail_is_written(self):
        proc, sheets = _processor()
        proc._process_one(_raw("<a@jds>"), {}, seen_ids=set())
        sheets.write_inquiry.assert_called_once()

    def test_already_recorded_mail_is_skipped(self):
        proc, sheets = _processor()
        proc._process_one(_raw("<a@jds>"), {}, seen_ids={"<a@jds>"})
        sheets.write_inquiry.assert_not_called()

    def test_same_mail_twice_in_one_cycle_writes_once(self):
        proc, sheets = _processor()
        seen = set()
        proc._process_one(_raw("<a@jds>"), {}, seen_ids=seen)
        proc._process_one(_raw("<a@jds>"), {}, seen_ids=seen)
        self.assertEqual(sheets.write_inquiry.call_count, 1)

    def test_different_message_ids_both_written(self):
        proc, sheets = _processor()
        seen = set()
        proc._process_one(_raw("<a@jds>"), {}, seen_ids=seen)
        proc._process_one(_raw("<b@jds>"), {}, seen_ids=seen)
        self.assertEqual(sheets.write_inquiry.call_count, 2)

    def test_missing_message_id_is_not_treated_as_seen(self):
        # Older rows have no recorded ID; a blank must never match them.
        proc, sheets = _processor()
        proc._process_one(_raw(""), {}, seen_ids={""})
        sheets.write_inquiry.assert_called_once()


class TestSheetPersistence(unittest.TestCase):
    def test_row_carries_the_message_id(self):
        from datetime import datetime
        inq = Inquiry(id="i", received_at=datetime(2026, 8, 31, 12, 0),
                      customer_name="三宅", customer_email="a@b.c",
                      inquiry_property_name="オリーブ201", inquiry_property_url="",
                      raw_body="", message_id="<a@jds>")
        row = inq.to_sheets_row()
        headers = Inquiry.sheets_headers()
        self.assertEqual(len(row), len(headers))
        self.assertEqual(row[headers.index("受信メッセージID")], "<a@jds>")

    def test_seen_ids_skip_the_header_and_blanks(self):
        from src.integrations.sheets_client import SheetsClient
        client = SheetsClient.__new__(SheetsClient)
        ws = MagicMock()
        ws.col_values.return_value = ["受信メッセージID", "<a@jds>", "", "  ", "<b@jds>"]
        client._ws = MagicMock(return_value=ws)
        client._retry = lambda fn, **kw: fn()
        self.assertEqual(client.read_seen_message_ids(), {"<a@jds>", "<b@jds>"})


if __name__ == "__main__":
    unittest.main()
