"""
Ingestion must not depend on the unread flag, and the auto-send switch must be
the one the operator can actually see.

Both were live faults: two SUUMO reflections for ボニートロッサI107 (2026-09-07)
were read in the mailbox before the poller ran and were then never ingested, and
the 設定-sheet switch read a key the sheet never contained.
"""
import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock

from src.core.models import Inquiry
from src.email_builder.send_gate import (GATE_AUTO, GATE_BLOCKED, GATE_CONFIRM,
                                         SendGate)
from src.integrations import gmail_client
from src.integrations.gmail_client import GmailClient


class _Clean:
    is_clean = True
    discriminatory_reason = ""
    ng_hits = []


class _Dirty:
    is_clean = False
    discriminatory_reason = "x"
    ng_hits = []


def _inquiry(**kw):
    base = dict(id="i", received_at=None, customer_name="n",
                customer_email="a@b.c", inquiry_property_name="p",
                inquiry_property_url="", raw_body="", is_vacant=True)
    base.update(kw)
    return Inquiry(**base)


class TestFetchIgnoresReadState(unittest.TestCase):
    """A reflection someone opened first must still be ingested."""

    def setUp(self):
        self.captured = {}

        class _MB:
            def __init__(_s, host): pass
            def login(_s, *a, **k): return _s
            def __enter__(_s): return _s
            def __exit__(_s, *a): return False
            def fetch(_s, criteria, mark_seen=True, bulk=False):
                self.captured["criteria"] = criteria
                self.captured["mark_seen"] = mark_seen
                return iter(())

        self._orig = gmail_client.MailBox
        gmail_client.MailBox = _MB
        self.addCleanup(lambda: setattr(gmail_client, "MailBox", self._orig))

    def test_does_not_filter_on_unread(self):
        GmailClient("a@b.c", "pw", "imap", "smtp", 465).fetch_recent()
        self.assertNotIn("seen", str(self.captured["criteria"]).lower())

    def test_does_not_mark_mail_as_read(self):
        """Marking seen before the rows are written loses mail on any error."""
        GmailClient("a@b.c", "pw", "imap", "smtp", 465).fetch_recent()
        self.assertFalse(self.captured["mark_seen"])

    def test_bounded_by_a_lookback_window(self):
        GmailClient("a@b.c", "pw", "imap", "smtp", 465).fetch_recent()
        d = date.today() - timedelta(days=gmail_client._LOOKBACK_DAYS)
        # imap_tools renders dates in IMAP form, e.g. "(SINCE 1-Sep-2026)"
        self.assertIn(f"{d.day}-{d.strftime('%b')}-{d.year}",
                      str(self.captured["criteria"]))


class TestAutoSendSwitch(unittest.TestCase):
    """The 設定 sheet cell the operator edits must be the one that is read."""

    def test_sheet_key_matches_what_the_operator_sees(self):
        gate = SendGate(False)
        self.assertEqual(
            gate.evaluate(_inquiry(), _Clean(), _Clean(), {"自動送信有効": True}),
            GATE_AUTO)

    def test_switch_off_still_requires_confirmation(self):
        gate = SendGate(False)
        self.assertEqual(
            gate.evaluate(_inquiry(), _Clean(), _Clean(), {"自動送信有効": False}),
            GATE_CONFIRM)

    def test_unreadable_settings_sheet_fails_closed(self):
        """read_auto_send_conditions returns {} when the sheet cannot be read."""
        gate = SendGate(False)
        self.assertEqual(gate.evaluate(_inquiry(), _Clean(), _Clean(), {}),
                         GATE_CONFIRM)

    def test_emergency_lock_overrides_the_sheet(self):
        gate = SendGate(True)
        self.assertEqual(
            gate.evaluate(_inquiry(), _Clean(), _Clean(), {"自動送信有効": True}),
            GATE_CONFIRM)

    def test_ng_content_still_blocks_even_with_auto_send_on(self):
        gate = SendGate(False)
        self.assertEqual(
            gate.evaluate(_inquiry(), _Dirty(), _Clean(), {"自動送信有効": True}),
            GATE_BLOCKED)


class TestFollowupStatusReflectsReality(unittest.TestCase):
    def test_new_inquiry_is_not_marked_as_being_followed_up(self):
        self.assertEqual(_inquiry().followup_status, "未送信")

    def test_blank_cell_reads_back_as_not_sent(self):
        rec = {"ID": "i", "顧客名": "n", "メールアドレス": "a@b.c",
               "追客ステータス": ""}
        self.assertEqual(Inquiry.from_sheets_record(rec).followup_status, "未送信")

    def test_an_active_sequence_is_preserved(self):
        rec = {"ID": "i", "顧客名": "n", "メールアドレス": "a@b.c",
               "追客ステータス": "追客中"}
        self.assertEqual(Inquiry.from_sheets_record(rec).followup_status, "追客中")

    def test_untouched_row_is_not_a_followup_candidate(self):
        """未送信 must never be picked up by the follow-up scanner."""
        self.assertNotEqual(_inquiry().followup_status, "追客中")


if __name__ == "__main__":
    unittest.main()
