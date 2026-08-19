from __future__ import annotations
import logging
import time
from datetime import datetime
from typing import Any

import gspread
import requests
from google.auth import exceptions as google_auth_exceptions
from google.oauth2.service_account import Credentials

from src.core.models import Inquiry, NGHit

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Worth retrying: Sheets quota/5xx (APIError), network blips, and OAuth token
# refresh hiccups. Note APIError is a *subclass* of GSpreadException, so catching
# GSpreadException here would also swallow permanent faults like a missing tab or
# a duplicated header — those need a human, and retrying only delays the log.
_TRANSIENT_ERRORS = (
    gspread.exceptions.APIError,
    requests.exceptions.RequestException,
    google_auth_exceptions.TransportError,
    google_auth_exceptions.RefreshError,
)

# ── row positions (1-indexed, row 1 = header) ──────────────────────────────
_VACANCY_COL = 7        # "空室有無"
_STATUS_COL = 8         # "ステータス"
_DRAFT_COL = 9          # "AI返信文案"
_FOLLOWUP_COL = 14      # "追客ステータス"
_NEXT_FOLLOWUP_COL = 15 # "次回追客予定日"
_SENT_COL = 16          # "送信日時"
_FOLLOWUP_COUNT_COL = 17  # "追客回数"
_MEMO_COL = 18          # "担当者メモ"


class SheetsClient:
    def __init__(self, service_account_json: str, spreadsheet_id: str, sheet_names: dict):
        creds = Credentials.from_service_account_file(service_account_json, scopes=_SCOPES)
        gc = gspread.authorize(creds)
        self._names = sheet_names
        # Retried like every other Sheets call. This is the first network call
        # the service makes at boot, and Google returns a transient 503 here
        # often enough to matter — observed on the very first deploy attempt.
        # Unretried, a blip becomes a failed start, and systemd gives up after 5.
        self._ss = self._retry(lambda: gc.open_by_key(spreadsheet_id))
        self._ensure_headers()

    # ── internal helpers ────────────────────────────────────────────────────

    def _ws(self, key: str) -> gspread.Worksheet:
        title = self._names[key]
        try:
            return self._ss.worksheet(title)
        except gspread.exceptions.WorksheetNotFound as e:
            raise RuntimeError(
                f"スプレッドシートにシート「{title}」がありません。"
                f"タブ名を確認するか setup_sheets.py を実行してください。"
            ) from e

    def _ensure_headers(self) -> None:
        ws = self._ws("inquiries")
        # Only seed headers into a genuinely empty sheet. The previous check
        # ("A1 is not 'ID'") inserted a row on top of existing data — and with
        # the poller plus both gunicorn workers booting together, three times.
        if self._retry(lambda: ws.get_all_values()):
            return
        self._retry(lambda: ws.append_row(Inquiry.sheets_headers(),
                                          value_input_option="RAW"))
        logger.info("Created header row on %s", ws.title)

    def _retry(self, fn, retries: int = 3, delay: float = 2.0):
        for attempt in range(retries):
            try:
                return fn()
            except _TRANSIENT_ERRORS as e:
                if attempt == retries - 1:
                    raise
                logger.warning("Sheets call failed (attempt %d/%d): %s",
                               attempt + 1, retries, e)
                time.sleep(delay * (attempt + 1))

    def _records(self, ws: gspread.Worksheet) -> list[dict]:
        """`get_all_records` with a readable message for the usual sheet-edit slip.

        gspread raises when the header row contains duplicates — two blank-header
        columns count — which is easy to cause by hand-editing and otherwise
        surfaces as an opaque traceback.
        """
        try:
            return self._retry(lambda: ws.get_all_records())
        except gspread.exceptions.APIError:
            raise
        except gspread.exceptions.GSpreadException as e:
            raise RuntimeError(
                f"シート「{ws.title}」の見出し行を読み取れません"
                f"（見出しの重複や空欄が原因です）: {e}"
            ) from e

    # ── inquiry write / update ──────────────────────────────────────────────

    def write_inquiry(self, inquiry: Inquiry) -> None:
        ws = self._ws("inquiries")
        self._retry(lambda: ws.append_row(inquiry.to_sheets_row(), value_input_option="RAW"))
        logger.info("Wrote inquiry %s to Sheets", inquiry.id)

    def find_inquiry_row(self, inquiry_id: str) -> int | None:
        """Return 1-based row index for the given inquiry ID, or None."""
        ws = self._ws("inquiries")
        ids = self._retry(lambda: ws.col_values(1))
        try:
            return ids.index(inquiry_id) + 1
        except ValueError:
            return None

    def update_inquiry_field(self, inquiry_id: str, col: int, value: Any) -> None:
        row = self.find_inquiry_row(inquiry_id)
        if row is None:
            logger.error("Inquiry %s not found in Sheets", inquiry_id)
            return
        ws = self._ws("inquiries")
        self._retry(lambda: ws.update_cell(row, col, value))

    def update_status(self, inquiry_id: str, status: str) -> None:
        self.update_inquiry_field(inquiry_id, _STATUS_COL, status)
        logger.info("Status → %s for %s", status, inquiry_id)

    def update_vacancy(self, inquiry_id: str, label: str) -> None:
        """Reflect looked-up vacancy (initial row is written before lookup)."""
        self.update_inquiry_field(inquiry_id, _VACANCY_COL, label)

    def update_draft(self, inquiry_id: str, draft: str) -> None:
        self.update_inquiry_field(inquiry_id, _DRAFT_COL, draft)

    def mark_sent(self, inquiry_id: str, message_id: str) -> None:
        self.update_inquiry_field(inquiry_id, _SENT_COL,
                                  datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.update_status(inquiry_id, "送信済み")

    def stop_followup(self, inquiry_id: str) -> None:
        self.update_inquiry_field(inquiry_id, _FOLLOWUP_COL, "追客停止")

    # ── NG word management ──────────────────────────────────────────────────

    def read_ng_words(self) -> list[dict]:
        """Return list of {"word": str, "category": str}."""
        return [r for r in self._records(self._ws("ng_words")) if r.get("ワード")]

    # ── send log ────────────────────────────────────────────────────────────

    def write_send_log(self, inquiry_id: str, recipient: str,
                       subject: str, message_id: str, mail_type: str) -> None:
        ws = self._ws("send_log")
        row = [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            inquiry_id, recipient, subject, message_id, mail_type,
        ]
        self._retry(lambda: ws.append_row(row, value_input_option="RAW"))

    def write_review_log(self, inquiry_id: str, reason: str, ng_words: str) -> None:
        ws = self._ws("review_log")
        row = [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            inquiry_id, reason, ng_words,
        ]
        self._retry(lambda: ws.append_row(row, value_input_option="RAW"))

    # ── send-mode config ────────────────────────────────────────────────────

    def read_auto_send_conditions(self) -> dict:
        """Return dict of condition_key → bool from 設定 sheet."""
        try:
            rows = self._records(self._ws("config"))
        except Exception:
            # Failing closed is right, but the old bare `except: return {}` did it
            # silently — an unreadable 設定 sheet looked identical to one that
            # simply had auto-send switched off.
            logger.exception("Could not read the 設定 sheet — treating every "
                             "auto-send condition as off")
            return {}

        conditions: dict[str, bool] = {}
        for r in rows:
            key = str(r.get("設定キー", "")).strip()
            if key:
                # gspread returns a real bool for a TRUE cell, so coerce to str
                # before comparing — `.strip()` on a bool used to raise here.
                conditions[key] = str(r.get("値", "")).strip().lower() == "true"
        return conditions

    # ── lookup helpers for reply detection ─────────────────────────────────

    def find_inquiry_by_message_id(self, message_id: str) -> str | None:
        """Return inquiry_id whose send_message_id matches, or None."""
        ws = self._ws("send_log")
        mids = self._retry(lambda: ws.col_values(5))  # message_id column
        try:
            row_idx = mids.index(message_id)
        except ValueError:
            return None
        ids = self._retry(lambda: ws.col_values(2))
        return ids[row_idx] if row_idx < len(ids) else None

    # ── inquiry reads (admin panel + scheduler) ──────────────────────────────

    def read_inquiries(self) -> list[dict]:
        """Return all inquiry rows as dicts (keys = headers)."""
        return self._records(self._ws("inquiries"))

    def get_inquiry(self, inquiry_id: str) -> dict | None:
        for rec in self.read_inquiries():
            if str(rec.get("ID", "")).strip() == inquiry_id:
                return rec
        return None

    def set_draft(self, inquiry_id: str, draft: str) -> None:
        self.update_inquiry_field(inquiry_id, _DRAFT_COL, draft)

    def set_memo(self, inquiry_id: str, memo: str) -> None:
        self.update_inquiry_field(inquiry_id, _MEMO_COL, memo)

    # ── follow-up scheduling ─────────────────────────────────────────────────

    def schedule_followup(self, inquiry_id: str, next_at: datetime, count: int) -> None:
        """Record the next follow-up due time and current follow-up count."""
        self.update_inquiry_field(inquiry_id, _NEXT_FOLLOWUP_COL,
                                  next_at.strftime("%Y-%m-%d %H:%M:%S"))
        self.update_inquiry_field(inquiry_id, _FOLLOWUP_COUNT_COL, count)
        self.update_inquiry_field(inquiry_id, _FOLLOWUP_COL, "追客中")

    def advance_followup(self, inquiry_id: str, count: int,
                         next_at: datetime | None, status: str) -> None:
        """After a follow-up mail is sent: bump count, set next due (or clear), set status."""
        self.update_inquiry_field(inquiry_id, _FOLLOWUP_COUNT_COL, count)
        self.update_inquiry_field(
            inquiry_id, _NEXT_FOLLOWUP_COL,
            next_at.strftime("%Y-%m-%d %H:%M:%S") if next_at else "")
        self.update_inquiry_field(inquiry_id, _FOLLOWUP_COL, status)

    def read_followup_candidates(self, now: datetime) -> list[dict]:
        """Return inquiry rows whose follow-up is due (追客中 and 次回追客予定日 <= now)."""
        due: list[dict] = []
        for rec in self.read_inquiries():
            if str(rec.get("追客ステータス", "")).strip() != "追客中":
                continue
            nxt = str(rec.get("次回追客予定日", "")).strip()
            if not nxt:
                continue
            parsed = None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    parsed = datetime.strptime(nxt, fmt)
                    break
                except ValueError:
                    continue
            if parsed and parsed <= now:
                due.append(rec)
        return due

    def write_followup_log(self, inquiry_id: str, mail_type: str, result: str) -> None:
        ws = self._ws("followup_log")
        row = [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), inquiry_id, mail_type, result]
        self._retry(lambda: ws.append_row(row, value_input_option="RAW"))
