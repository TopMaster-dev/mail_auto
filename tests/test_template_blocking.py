"""
A broken template stops sending; it never quietly reverts (client, 2026-10-07).

The client's reasoning: with a silent fallback they would believe their new
wording was live while customers kept receiving the old one, with no way to
notice. Stopping is recoverable; an unnoticed wrong mail is not.

Recovery needs no per-inquiry action. Ingestion halts *before* anything is
recorded, so the reflections stay in the mailbox and the next cycle picks them
up once the sheet is fixed.
"""
import unittest
from unittest.mock import MagicMock

from src.core.inquiry_processor import InquiryProcessor
from src.email_builder import template_store as ts
from src.scheduling.followup_scheduler import FollowupScheduler


def _store(blocking_for=None, errors=None):
    s = MagicMock()
    s.blocking_errors.side_effect = lambda kind: (
        ["差し込み項目 {物件名称} は使用できません。"] if kind == blocking_for else [])
    s.all_errors.return_value = errors if errors is not None else (
        {blocking_for: ["差し込み項目 {物件名称} は使用できません。"]} if blocking_for else {})
    return s


def _processor(store):
    p = InquiryProcessor.__new__(InquiryProcessor)
    p._sheets = MagicMock()
    p._gmail = MagicMock()
    p._checker = MagicMock()
    p._templates = store
    p._last_template_status = None
    p._wp = MagicMock()
    p._scorer = MagicMock()
    p._wp_refresh_minutes = 30
    return p


class TestIngestionStopsOnABrokenFirstTemplate(unittest.TestCase):
    def test_no_mail_is_read_while_the_1st_template_is_broken(self):
        p = _processor(_store(blocking_for=ts.FIRST))
        p.run_cycle()
        p._gmail.fetch_recent.assert_not_called()

    def test_nothing_is_recorded_so_there_is_nothing_to_repair(self):
        p = _processor(_store(blocking_for=ts.FIRST))
        p.run_cycle()
        p._sheets.write_inquiry.assert_not_called()

    def test_a_valid_template_lets_the_cycle_run(self):
        p = _processor(_store())
        p._gmail.fetch_recent.return_value = []
        p.run_cycle()
        p._gmail.fetch_recent.assert_called_once()

    def test_a_broken_2nd_does_not_stop_new_inquiries(self):
        """Only the 1st mail gates ingestion."""
        p = _processor(_store(blocking_for=ts.SECOND))
        p._gmail.fetch_recent.return_value = []
        p.run_cycle()
        p._gmail.fetch_recent.assert_called_once()

    def test_no_store_configured_behaves_as_before(self):
        p = _processor(None)
        p._gmail.fetch_recent.return_value = []
        p.run_cycle()
        p._gmail.fetch_recent.assert_called_once()


class TestStatusIsPublishedForThePanel(unittest.TestCase):
    def test_errors_are_written_to_the_settings_sheet(self):
        p = _processor(_store(blocking_for=ts.FIRST))
        p._templates_ready()
        key, value = p._sheets.set_config.call_args[0][:2]
        self.assertEqual(key, InquiryProcessor.TEMPLATE_STATUS_KEY)
        self.assertIn("物件名称", value)

    def test_a_healthy_set_publishes_the_ok_marker(self):
        p = _processor(_store())
        p._templates_ready()
        self.assertEqual(p._sheets.set_config.call_args[0][1],
                         InquiryProcessor._TEMPLATE_OK)

    def test_an_unchanged_status_is_not_rewritten_every_cycle(self):
        p = _processor(_store())
        p._templates_ready()
        p._templates_ready()
        p._templates_ready()
        self.assertEqual(p._sheets.set_config.call_count, 1)

    def test_a_change_is_written(self):
        store = _store()
        p = _processor(store)
        p._templates_ready()
        store.all_errors.return_value = {ts.FIRST: ["壊れました"]}
        store.blocking_errors.side_effect = lambda k: ["壊れました"] if k == ts.FIRST else []
        p._templates_ready()
        self.assertEqual(p._sheets.set_config.call_count, 2)


class TestFollowUpsHoldTheirSlot(unittest.TestCase):
    def _scheduler(self, store):
        s = FollowupScheduler.__new__(FollowupScheduler)
        s._sheets = MagicMock()
        s._gmail = MagicMock()
        s._wp = MagicMock()
        s._generator = MagicMock()
        s._scorer = MagicMock()
        s._checker = MagicMock()
        s._company = {"staff_name": "新家"}
        s._templates = store
        s._steps = [2, 3]
        s._max = 2
        return s

    def test_a_broken_2nd_template_sends_nothing(self):
        s = self._scheduler(_store(blocking_for=ts.SECOND))
        s._resolve_property = MagicMock(return_value=None)
        s._build_segments = MagicMock(return_value=("", "", []))
        rec = {"ID": "i1", "メールアドレス": "a@b.c", "追客回数": "1",
               "顧客名": "n", "問い合わせ物件": "p"}
        s._send_followup(rec)
        s._gmail.send.assert_not_called()

    def test_the_schedule_is_left_untouched_so_it_retries(self):
        """Not advanced, not stopped — fixing the sheet is the whole repair."""
        s = self._scheduler(_store(blocking_for=ts.SECOND))
        s._resolve_property = MagicMock(return_value=None)
        s._build_segments = MagicMock(return_value=("", "", []))
        rec = {"ID": "i1", "メールアドレス": "a@b.c", "追客回数": "1",
               "顧客名": "n", "問い合わせ物件": "p"}
        s._send_followup(rec)
        s._sheets.advance_followup.assert_not_called()
        s._sheets.stop_followup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
