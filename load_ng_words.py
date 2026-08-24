# -*- coding: utf-8 -*-
"""
Load config/ng_words.yaml into the スプレッドシート「NGワード」シート.

Merges rather than replaces: any word the operator added by hand is kept, so
running this twice is safe and never discards their edits.

    python load_ng_words.py            # dry run — shows the diff, changes nothing
    python load_ng_words.py --apply    # back up the sheet, then write
"""
from __future__ import annotations

import argparse
import io
import sys
from datetime import datetime
from pathlib import Path

import yaml

from src.config_loader import load_config
from src.integrations.sheets_client import SheetsClient

_DEF = Path(__file__).parent / "config" / "ng_words.yaml"
_HEADER = ["ワード", "カテゴリ", "適用段階"]
_STAGE_ALL = "初回から"
_STAGE_FOLLOWUP = "2通目以降"


def _console():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def load_definition() -> list[tuple[str, str, str]]:
    """Return (word, category, stage) for every defined word."""
    data = yaml.safe_load(io.open(_DEF, encoding="utf-8"))
    rows = [(w, cat, _STAGE_ALL)
            for cat, words in data["categories"].items() for w in words]
    rows += [(w, cat, _STAGE_FOLLOWUP)
             for cat, words in (data.get("followup_only") or {}).items() for w in words]
    return rows


def main() -> None:
    _console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write to the sheet (otherwise dry run)")
    args = ap.parse_args()

    defined = load_definition()
    cfg = load_config()
    sheets = SheetsClient(
        service_account_json=cfg["sheets"]["service_account_json"],
        spreadsheet_id=cfg["sheets"]["spreadsheet_id"],
        sheet_names=cfg["sheets"]["names"])

    ws = sheets._ws("ng_words")
    existing_rows = ws.get_all_values()
    existing = {r[0].strip(): ((r[1].strip() if len(r) > 1 else ""),
                               (r[2].strip() if len(r) > 2 else _STAGE_ALL))
                for r in existing_rows[1:] if r and r[0].strip()}

    merged: dict[str, tuple[str, str]] = dict(existing)
    added, restaged = [], []
    for word, category, stage in defined:
        if word not in merged:
            merged[word] = (category, stage)
            added.append((word, category))
        elif merged[word][1] != stage:
            # The definition owns the stage; a re-run corrects a drifted value.
            merged[word] = (merged[word][0] or category, stage)
            restaged.append((word, stage))

    kept_manual = [w for w in existing if w not in {d[0] for d in defined}]

    print(f"シート現在      : {len(existing)} 語")
    print(f"定義ファイル    : {len(defined)} 語")
    print(f"新規追加        : {len(added)} 語")
    print(f"手動追加を維持  : {len(kept_manual)} 語  {kept_manual if kept_manual else ''}")
    print(f"段階変更        : {len(restaged)} 語  "
          f"{[w for w, _ in restaged] if restaged else ''}")
    print(f"適用後の合計    : {len(merged)} 語")
    print(f"  うち2通目以降 : "
          f"{sum(1 for _, st in merged.values() if st == _STAGE_FOLLOWUP)} 語")

    if added:
        print("\n追加される語（カテゴリ別）:")
        by_cat: dict[str, list[str]] = {}
        for word, cat in added:
            by_cat.setdefault(cat, []).append(word)
        for cat, words in by_cat.items():
            print(f"  [{cat}] {'、'.join(words)}")

    if not added and not restaged:
        print("\n変更はありません。")
    if not args.apply:
        print("\n※ ドライラン。書き込むには --apply を付けて実行してください。")
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    backup = sheets._ss.duplicate_sheet(ws.id, new_sheet_name=f"NGワード_backup_{stamp}")
    assert len(backup.get_all_values()) == len(existing_rows), "バックアップ行数不一致"
    print(f"\nバックアップ作成: NGワード_backup_{stamp}")

    rows = [_HEADER]
    order = [c for c in yaml.safe_load(io.open(_DEF, encoding="utf-8"))["categories"]]
    rows += sorted(([w, c, st] for w, (c, st) in merged.items()),
                   key=lambda r: (order.index(r[1]) if r[1] in order else len(order), r[0]))

    ws.clear()
    ws.update(rows, "A1", value_input_option="RAW")
    print(f"書き込み完了    : {len(rows) - 1} 語")

    reread = sheets.read_ng_words()
    print(f"読み戻し確認    : {len(reread)} 語")


if __name__ == "__main__":
    main()
