"""
Add the 判定方式 column to the NGワード sheet and set the agreed split.

強制停止 holds the mail on the word alone. 文脈判定 records the word but leaves the
decision to the AI's reading of the surrounding text.

The split below follows the table the client approved on 2026-09-11: words that
only ever appear in a problem stay 強制停止; words that also appear in a perfectly
ordinary enquiry become 文脈判定. Anything not listed stays 強制停止, so a word
added later is strict until someone decides otherwise.

The client can change any of it in the sheet afterwards — that is the point of
the column.

    python set_ng_modes.py            # show what would change
    python set_ng_modes.py --apply
"""
from __future__ import annotations

import io
import os
import sys

import yaml
from dotenv import load_dotenv

import gspread
from google.oauth2.service_account import Credentials

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
           "https://www.googleapis.com/auth/drive.readonly"]

MODE_FORCE = "強制停止"
MODE_CONTEXT = "文脈判定"

# Words that occur in genuine, routine enquiries as readily as in problems.
CONTEXT_JUDGED = {
    # クレーム系 — everyday words that are not inherently hostile. Real anger is
    # caught by the AI's complaint judgement regardless.
    "最悪", "ありえない", "納得できない", "対応が悪い", "評判悪い",
    # 契約・法的関連 — asking what the contract says is normal; threatening is not.
    "解約", "重要事項説明", "契約書", "特約", "原状回復", "退去費用", "敷金精算",
    # 審査・保証会社関連 — the case the client found: 「入居審査について教えてください」
    "審査", "保証会社", "保証人なし", "連帯保証人", "収入証明",
    # 修繕・設備 — 「騒音はどうですか」 is a question about a property, not a fault report.
    "修理", "故障", "騒音", "カビ", "害虫", "設備不良",
    # 金銭 — negotiating is ordinary; alleging overcharging is not.
    "高すぎる", "安くならないか", "値下げ", "家賃交渉",
    # 緊急性 — almost every enquiry contains one of these.
    "至急", "今すぐ", "早急", "緊急", "本日中", "すぐ連絡ください", "急ぎ",
    "今日中", "折り返し", "すぐ来て", "困っています", "助けてください", "早く",
}


def main() -> None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    load_dotenv()
    apply = "--apply" in sys.argv

    cfg = yaml.safe_load(io.open("config/settings.yaml", encoding="utf-8"))
    creds = Credentials.from_service_account_file(
        os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"), scopes=_SCOPES)
    ss = gspread.authorize(creds).open_by_key(os.getenv("SPREADSHEET_ID"))
    ws = ss.worksheet(cfg["sheets"]["names"]["ng_words"])

    values = ws.get_all_values()
    header = values[0]
    if "判定方式" not in header:
        col = len(header) + 1
        print(f"判定方式 列を追加します（{col}列目）")
    else:
        col = header.index("判定方式") + 1
        print(f"判定方式 列は既にあります（{col}列目）")

    word_col = header.index("ワード")
    modes, changed = [], 0
    for row in values[1:]:
        word = (row[word_col] if word_col < len(row) else "").strip()
        if not word:
            modes.append("")
            continue
        current = (row[col - 1] if col - 1 < len(row) else "").strip()
        want = MODE_CONTEXT if word in CONTEXT_JUDGED else MODE_FORCE
        modes.append(want)
        if current != want:
            changed += 1

    ctx = [m for m in modes if m == MODE_CONTEXT]
    print(f"\n対象 {len([m for m in modes if m])} 語")
    print(f"  強制停止: {len([m for m in modes if m == MODE_FORCE])} 語")
    print(f"  文脈判定: {len(ctx)} 語")
    print(f"  変更が必要: {changed} 語")

    missing = CONTEXT_JUDGED - {
        (r[word_col] if word_col < len(r) else "").strip() for r in values[1:]}
    if missing:
        print(f"\n⚠ シートに存在しない指定語（無視されます）: {'、'.join(sorted(missing))}")

    if not apply:
        print("\n--apply を付けると書き込みます。")
        return

    if "判定方式" not in header:
        if ws.col_count < col:
            ws.add_cols(col - ws.col_count)
        ws.update_cell(1, col, "判定方式")
    cells = [[m] for m in modes]
    ws.update(values=cells,
              range_name=gspread.utils.rowcol_to_a1(2, col) + ":"
                         + gspread.utils.rowcol_to_a1(1 + len(cells), col),
              value_input_option="RAW")
    print(f"\n書き込み完了（{len(cells)} 行）")


if __name__ == "__main__":
    main()
