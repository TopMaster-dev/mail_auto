"""
Lay out the テンプレート sheet: instructions, the placeholder list, and the
current wording as a copyable reference.

The editable rows are deliberately left EMPTY. An unset template means "use the
built-in text", so running this changes no mail that goes out — the client opts
in by pasting a reference row into the editable row and editing it.

Idempotent: re-running rewrites the layout without touching anything the client
has typed into the editable rows.

    python seed_templates.py            # show what would be written
    python seed_templates.py --apply
"""
from __future__ import annotations

import io
import os
import sys
from datetime import datetime

import yaml
from dotenv import load_dotenv

import gspread
from google.oauth2.service_account import Credentials

from src.core.models import Inquiry, Property
from src.email_builder import template_store as ts
from src.email_builder.assembler import EmailAssembler

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
           "https://www.googleapis.com/auth/drive.readonly"]

# Sentinel values used to build the reference text, then swapped back for the
# placeholder tokens. Chosen so they cannot occur in the real wording.
_MARK = {
    "顧客名": "〘NAME〙",
    "物件名": "〘PROP〙",
    "AI紹介文": "〘INTRO〙",
}


def _sample_inquiry() -> Inquiry:
    prop = Property(
        wp_id=1, name=_MARK["物件名"], url="〘URL〙", rent=64000, management_fee=0,
        layout="1R", nearest_station="", train_line="名鉄本線", city="知立市",
        walk_minutes=20, category=[], equipment=[], is_vacant=True,
        is_commission_free=False, area_sqm=50.0, building_type="",
        building_name=_MARK["物件名"], room_number="107", address="",
        access='名鉄本線「牛田」徒歩20分')
    return Inquiry(
        id="sample", received_at=datetime(2026, 1, 1),
        customer_name=_MARK["顧客名"], customer_email="sample@example.com",
        inquiry_property_name=_MARK["物件名"], inquiry_property_url="",
        raw_body="", is_vacant=True, matched_property=prop)


def _to_template(text: str, signature: str, staff: str = "") -> str:
    """Current wording → the same wording written with placeholders."""
    out = text.replace(signature.strip(), "{署名}")
    if staff:
        out = out.replace(staff, "{担当者名}")
    out = out.replace(_MARK["AI紹介文"], "{AI紹介文}")
    out = out.replace("〘URL〙", "{物件URL}")
    out = out.replace(_MARK["物件名"], "{物件名}")
    out = out.replace(_MARK["顧客名"], "{顧客名}")
    out = out.replace('名鉄本線「牛田」', "{最寄駅}")
    return out


def current_wording(company: dict) -> dict[str, tuple[str, str]]:
    """{kind: (subject, body)} for the built-in mails, as editable templates."""
    from src.email_builder.assembler import _SIGNATURE
    inq = _sample_inquiry()
    a = EmailAssembler(company, True)
    out = {}
    for kind, build in ((ts.FIRST, a.build_first_mail_parts),
                        (ts.SECOND, a.build_second_mail_parts),
                        (ts.THIRD, a.build_third_mail_parts)):
        subject, body = build(inq, _MARK["AI紹介文"], "", [])
        # The property block is data-shaped; one placeholder stands for it.
        section = a._property_section(inq, _MARK["AI紹介文"], [])
        body = body.replace(section.strip(), "{物件情報}")
        staff = str(company.get("staff_name", ""))
        out[kind] = (_to_template(subject, _SIGNATURE, staff),
                     _to_template(body, _SIGNATURE, staff))
    return out


def build_rows(company: dict) -> list[list[str]]:
    rows: list[list[str]] = [["テンプレート名", "内容"]]
    rows.append(["■ このシートについて",
                 "1st・2nd・3rdメールの件名と本文を編集できます。"
                 "空欄のままにすると、システム標準の文面が使用されます。"])
    rows.append(["■ 注意",
                 "差し込み項目の記述ミスやNGワードがある場合、そのメールの送信は"
                 "停止し、管理画面の上部に警告が表示されます。"
                 "修正すると自動的に再開します（1stは最大5分、2nd・3rdは最大30分）。"])
    rows.append(["■ 差し込み項目", "下記の項目が件名・本文で使用できます（半角の { } で記述）"])
    rows.extend(ts.placeholder_help())
    rows.append(["", ""])
    rows.append(["■ 編集欄", "ここから下を編集してください（空欄＝標準文面）"])
    for kind in ts.KINDS:
        rows.append([ts.SUBJECT_ROW[kind], ""])
        rows.append([ts.BODY_ROW[kind], ""])
    rows.append(["", ""])
    rows.append(["■ 参考：現在の文面",
                 "下記は現在使用されている文面です。変更したい場合は、"
                 "該当する内容をコピーして上の編集欄に貼り付けてから編集してください。"
                 "（この行自体はシステムには使用されません）"])
    wording = current_wording(company)
    for kind in ts.KINDS:
        subject, body = wording[kind]
        rows.append([f"（参考）{ts.SUBJECT_ROW[kind]}", subject])
        rows.append([f"（参考）{ts.BODY_ROW[kind]}", body])
    return rows


def main() -> None:
    # Deliberately not at module level: importing this file must not replace
    # stdout or read .env — doing so tears down pytest's capture for the whole
    # session, which is exactly what setup_sheets.py does.
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    load_dotenv()

    apply = "--apply" in sys.argv
    cfg = yaml.safe_load(io.open("config/settings.yaml", encoding="utf-8"))
    rows = build_rows(cfg["company"])

    print(f"{'適用' if apply else 'ドライラン'}：{len(rows)} 行")
    for name, content in rows:
        preview = content.replace("\n", "⏎")[:70]
        print(f"  {name:<28} | {preview}")
    if not apply:
        print("\n--apply を付けると書き込みます。")
        return

    creds = Credentials.from_service_account_file(
        os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"), scopes=_SCOPES)
    ss = gspread.authorize(creds).open_by_key(os.getenv("SPREADSHEET_ID"))
    ws = ss.worksheet(cfg["sheets"]["names"]["templates"])

    existing = {str(r.get("テンプレート名", "")).strip(): str(r.get("内容", ""))
                for r in ws.get_all_records()} if ws.get_all_values() else {}
    kept = 0
    for row in rows:
        typed = existing.get(row[0], "")
        if typed and not row[1]:       # never clobber what the client typed
            row[1] = typed
            kept += 1

    ws.clear()
    ws.update(values=rows, range_name="A1", value_input_option="RAW")
    print(f"\n書き込み完了（既存の入力 {kept} 件を保持）")


if __name__ == "__main__":
    main()
