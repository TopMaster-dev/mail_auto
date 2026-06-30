# -*- coding: utf-8 -*-
"""
One-time Google Sheets setup.

Creates every worksheet tab the system expects, with the correct header row,
and seeds the NGワード and 設定 sheets with sensible defaults.

Idempotent: existing tabs are left in place (headers ensured only when the tab
is empty). Safe to run more than once.

Usage:
    python setup_sheets.py
"""
from __future__ import annotations
import io
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

import gspread
from google.oauth2.service_account import Credentials

from src.core.models import Inquiry

# Force UTF-8 output on Windows (avoids cp932 encode errors)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

load_dotenv()
ROOT = Path(__file__).parent

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Header rows for every tab (keys match settings.yaml → sheets.names)
_HEADERS: dict[str, list[str]] = {
    "inquiries": Inquiry.sheets_headers(),
    "ng_words": ["ワード", "カテゴリ"],
    "templates": ["テンプレート名", "内容"],
    "send_log": ["送信日時", "問い合わせID", "宛先", "件名", "Message-ID", "メール種別"],
    "followup_log": ["日時", "問い合わせID", "追客種別", "結果"],
    "review_log": ["日時", "問い合わせID", "理由", "検出ワード"],
    "config": ["設定キー", "値", "説明"],
}

# Seed NGワード — starter set; the operator can edit freely in the sheet
_NG_SEED = [
    ["クレーム", "クレーム系"],
    ["弁護士", "契約・法的関連"],
    ["訴訟", "契約・法的関連"],
    ["水漏れ", "修繕・設備トラブル"],
    ["故障", "修繕・設備トラブル"],
    ["返金", "返金・金銭関連"],
    ["至急", "緊急性"],
    ["緊急", "緊急性"],
    ["審査", "審査・保証会社関連"],
    ["外国人不可", "差別的表現"],
]

# Seed 設定 — auto-send stays OFF until explicitly enabled after full testing
_CONFIG_SEED = [
    ["自動送信有効", "false", "trueにすると条件を満たすメールを自動送信します（テスト中はfalse）"],
]


def load_cfg() -> dict:
    with open(ROOT / "config" / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> None:
    cfg = load_cfg()
    names: dict = cfg["sheets"]["names"]
    sid = os.environ["SPREADSHEET_ID"]

    creds = Credentials.from_service_account_file("service_account.json", scopes=_SCOPES)
    gc = gspread.authorize(creds)
    ss = gc.open_by_key(sid)
    print(f"Opened spreadsheet: {ss.title}")

    existing = {ws.title: ws for ws in ss.worksheets()}

    for key, title in names.items():
        headers = _HEADERS.get(key)
        if headers is None:
            continue

        if title in existing:
            ws = existing[title]
            print(f"  exists  : {title}")
        else:
            ws = ss.add_worksheet(title=title, rows=200, cols=max(20, len(headers)))
            print(f"  created : {title}")

        # Ensure header row when the tab is empty
        first_cell = ws.acell("A1").value
        if not first_cell:
            ws.append_row(headers, value_input_option="RAW")
            print(f"            header set ({len(headers)} cols)")

            if key == "ng_words":
                ws.append_rows(_NG_SEED, value_input_option="RAW")
                print(f"            seeded {len(_NG_SEED)} NG words")
            elif key == "config":
                ws.append_rows(_CONFIG_SEED, value_input_option="RAW")
                print(f"            seeded {len(_CONFIG_SEED)} config row(s)")

    # Remove the default empty tab if it is still present and now redundant
    default = existing.get("シート1") or existing.get("Sheet1")
    if default is not None and len(ss.worksheets()) > 1:
        if not (default.acell("A1").value):
            ss.del_worksheet(default)
            print("  removed : default empty tab")

    print("\nDone. Tabs now present:")
    for ws in ss.worksheets():
        print(f"  - {ws.title}")


if __name__ == "__main__":
    main()
