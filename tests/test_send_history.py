"""
Every mail sent for an inquiry is kept, body and all (client item ④, 2026-09-14).

The inquiry row holds one draft cell, so a 2nd or 3rd follow-up left no trace of
what the 1st actually said. Once follow-ups run automatically, staff taking a
phone call need to see what has already been sent.
"""
import unittest
from unittest.mock import MagicMock

from src.integrations.sheets_client import SheetsClient, _MAX_CELL_CHARS


def _client(rows=None):
    sc = SheetsClient.__new__(SheetsClient)
    sc._ws = MagicMock()
    sc._retry = lambda fn: fn()
    sc._records = MagicMock(return_value=rows or [])
    return sc


class TestBodyIsRecorded(unittest.TestCase):
    def test_the_body_is_written_as_the_seventh_column(self):
        sc = _client()
        ws = sc._ws.return_value
        sc.write_send_log("i1", "a@b.c", "件名", "<mid>", "2nd", "本文です")
        row = ws.append_row.call_args[0][0]
        self.assertEqual(len(row), 7)
        self.assertEqual(row[1:], ["i1", "a@b.c", "件名", "<mid>", "2nd", "本文です"])

    def test_the_header_has_a_matching_column(self):
        """Read the literal without importing: setup_sheets replaces sys.stdout
        at module level, which would tear down pytest's capture."""
        import ast, io
        tree = ast.parse(io.open("setup_sheets.py", encoding="utf-8").read())
        # Only the send_log entry: the dict as a whole is not a literal
        # ("inquiries" calls Inquiry.sheets_headers()).
        send_log = next(
            ast.literal_eval(value)
            for node in ast.walk(tree) if isinstance(node, ast.Dict)
            for key, value in zip(node.keys, node.values)
            if isinstance(key, ast.Constant) and key.value == "send_log")
        self.assertEqual(send_log[6], "本文")
        self.assertEqual(len(send_log), 7)

    def test_a_missing_body_is_written_as_empty_not_dropped(self):
        """The column must still be present, or later rows would shift."""
        sc = _client()
        ws = sc._ws.return_value
        sc.write_send_log("i1", "a@b.c", "件名", "<mid>", "1st")
        self.assertEqual(len(ws.append_row.call_args[0][0]), 7)

    def test_an_enormous_body_cannot_fail_the_write(self):
        """A Sheets cell caps at 50,000 chars; losing the send record is worse."""
        sc = _client()
        ws = sc._ws.return_value
        sc.write_send_log("i1", "a@b.c", "件名", "<mid>", "1st", "あ" * 60000)
        self.assertEqual(len(ws.append_row.call_args[0][0][6]), _MAX_CELL_CHARS)


class TestHistoryIsPerInquiry(unittest.TestCase):
    ROWS = [
        {"送信日時": "2026-09-09 11:04", "問い合わせID": "A", "メール種別": "1st", "本文": "A-1st"},
        {"送信日時": "2026-09-11 11:29", "問い合わせID": "A", "メール種別": "2nd", "本文": "A-2nd"},
        {"送信日時": "2026-09-10 09:00", "問い合わせID": "B", "メール種別": "1st", "本文": "B-1st"},
        {"送信日時": "2026-09-14 11:29", "問い合わせID": "A", "メール種別": "3rd", "本文": "A-3rd"},
    ]

    def test_only_that_inquiry_is_returned(self):
        got = _client(self.ROWS).read_send_log_for("A")
        self.assertEqual([r["メール種別"] for r in got], ["1st", "2nd", "3rd"])

    def test_bodies_are_preserved_separately(self):
        got = _client(self.ROWS).read_send_log_for("A")
        self.assertEqual([r["本文"] for r in got], ["A-1st", "A-2nd", "A-3rd"])

    def test_sent_order_is_preserved(self):
        got = _client(self.ROWS).read_send_log_for("A")
        self.assertEqual(got[0]["送信日時"], "2026-09-09 11:04")
        self.assertEqual(got[-1]["送信日時"], "2026-09-14 11:29")

    def test_an_inquiry_with_no_sends_returns_empty(self):
        self.assertEqual(_client(self.ROWS).read_send_log_for("Z"), [])

    def test_ids_are_matched_exactly_not_by_prefix(self):
        rows = [{"問い合わせID": "A1", "メール種別": "1st"},
                {"問い合わせID": "A", "メール種別": "2nd"}]
        got = _client(rows).read_send_log_for("A")
        self.assertEqual([r["メール種別"] for r in got], ["2nd"])


class TestNothingIsOverwritten(unittest.TestCase):
    def test_three_sends_append_three_rows(self):
        sc = _client()
        ws = sc._ws.return_value
        for kind in ("1st", "2nd", "3rd"):
            sc.write_send_log("i1", "a@b.c", "件名", f"<{kind}>", kind, f"{kind}本文")
        self.assertEqual(ws.append_row.call_count, 3)
        bodies = [c[0][0][6] for c in ws.append_row.call_args_list]
        self.assertEqual(bodies, ["1st本文", "2nd本文", "3rd本文"])


if __name__ == "__main__":
    unittest.main()
