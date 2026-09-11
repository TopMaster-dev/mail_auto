"""
A row left mid-processing must be visible, not silently stranded.

処理中 (formerly AI返信文生成済み) is written for a second or two between drafting
and the send gate. Rows are processed one at a time in a single process, so
nothing is legitimately 処理中 when a new cycle begins — anything still there was
interrupted by a crash or a restart, and would otherwise sit unsent forever with
no indication. Client asked (2026-09-11) to tell that apart from normal processing.
"""
import unittest
from unittest.mock import MagicMock

from src.core.inquiry_processor import InquiryProcessor


def _processor(rows):
    p = InquiryProcessor.__new__(InquiryProcessor)
    p._sheets = MagicMock()
    p._sheets.read_inquiries.return_value = rows
    return p


class TestSweepInterrupted(unittest.TestCase):
    def test_a_row_left_processing_becomes_interrupted(self):
        p = _processor([{"ID": "i1", "ステータス": "処理中"}])
        p._sweep_interrupted()
        p._sheets.update_status.assert_called_once_with("i1", "処理中断")

    def test_the_reason_is_recorded_for_the_operator(self):
        p = _processor([{"ID": "i1", "ステータス": "処理中"}])
        p._sweep_interrupted()
        p._sheets.write_review_log.assert_called_once()
        self.assertIn("中断", p._sheets.write_review_log.call_args[0][1])

    def test_settled_rows_are_untouched(self):
        p = _processor([
            {"ID": "a", "ステータス": "自動送信可"},
            {"ID": "b", "ステータス": "送信済み"},
            {"ID": "c", "ステータス": "NG検出"},
            {"ID": "d", "ステータス": "要確認"},
            {"ID": "e", "ステータス": "返信あり"},
        ])
        p._sweep_interrupted()
        p._sheets.update_status.assert_not_called()

    def test_an_already_flagged_row_is_not_re_flagged(self):
        p = _processor([{"ID": "i1", "ステータス": "処理中断"}])
        p._sweep_interrupted()
        p._sheets.update_status.assert_not_called()

    def test_rows_without_an_id_are_skipped(self):
        p = _processor([{"ID": "", "ステータス": "処理中"}])
        p._sweep_interrupted()
        p._sheets.update_status.assert_not_called()

    def test_an_unreadable_sheet_does_not_abort_the_cycle(self):
        p = InquiryProcessor.__new__(InquiryProcessor)
        p._sheets = MagicMock()
        p._sheets.read_inquiries.side_effect = RuntimeError("boom")
        p._sweep_interrupted()          # must not raise
        p._sheets.update_status.assert_not_called()

    def test_several_interrupted_rows_are_all_flagged(self):
        p = _processor([{"ID": "a", "ステータス": "処理中"},
                        {"ID": "b", "ステータス": "自動送信可"},
                        {"ID": "c", "ステータス": "処理中"}])
        p._sweep_interrupted()
        self.assertEqual(
            [c[0][0] for c in p._sheets.update_status.call_args_list], ["a", "c"])


if __name__ == "__main__":
    unittest.main()
