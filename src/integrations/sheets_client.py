from __future__ import annotations
import logging
import time
from datetime import datetime
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

from src.core.models import Inquiry, NGHit

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# ── row positions (1-indexed, row 1 = header) ──────────────────────────────
_STATUS_COL = 8      # Inquiries sheet "ステータス" column index (0-based = 7)
_DRAFT_COL = 9       # "AI返信文案"
_SENT_COL = 16       # "送信日時"
_FOLLOWUP_COL = 14   # "追客ステータス"
_MEMO_COL = 18       # "担当者メモ"


class SheetsClient:
    def __init__(self, service_account_json: str, spreadsheet_id: str, sheet_names: dict):
        creds = Credentials.from_service_account_file(service_account_json, scopes=_SCOPES)
        gc = gspread.authorize(creds)
        self._ss = gc.open_by_key(spreadsheet_id)
        self._names = sheet_names
        self._ensure_headers()

    # ── internal helpers ────────────────────────────────────────────────────

    def _ws(self, key: str) -> gspread.Worksheet:
        return self._ss.worksheet(self._names[key])

    def _ensure_headers(self) -> None:
        ws = self._ws("inquiries")
        if ws.row_count < 1 or ws.cell(1, 1).value != "ID":
            ws.insert_row(Inquiry.sheets_headers(), index=1)
            logger.info("Created header row on 問い合わせ一覧 sheet")

    def _retry(self, fn, retries: int = 3, delay: float = 2.0):
        for attempt in range(retries):
            try:
                return fn()
            except gspread.exceptions.APIError as e:
                if attempt == retries - 1:
                    raise
                logger.warning("Sheets API error (attempt %d): %s", attempt + 1, e)
                time.sleep(delay * (attempt + 1))

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
        ws = self._ws("ng_words")
        rows = self._retry(lambda: ws.get_all_records())
        return [r for r in rows if r.get("ワード")]

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
            ws = self._ws("config")
            rows = self._retry(lambda: ws.get_all_records())
            return {r["設定キー"]: r["値"].strip().lower() == "true"
                    for r in rows if r.get("設定キー")}
        except Exception:
            return {}

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
