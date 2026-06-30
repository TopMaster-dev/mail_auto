# -*- coding: utf-8 -*-
"""
Seed one realistic test inquiry into the spreadsheet so the admin panel has
something to review/send. The customer email is a TEST address, so clicking
"send" in the admin panel delivers to your own inbox (never a real customer).

Usage:
    python seed_test_inquiry.py [test-email@example.com]
"""
from __future__ import annotations
import io
import sys
import uuid
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from src.config_loader import load_config
from src.core.models import Inquiry, Property
from src.email_builder.assembler import EmailAssembler
from src.integrations.sheets_client import SheetsClient

TEST_EMAIL = sys.argv[1] if len(sys.argv) > 1 else "firstcol123@gmail.com"

# A real vacant, commission-free property (from rentmagazine.jp)
PROP = Property(
    wp_id=0, name="都市生活を楽しみたい方に",
    url="https://rentmagazine.jp/estate/%e9%83%bd%e5%b8%82%e7%94%9f%e6%b4%bb%e3%82%92%e6%a5%bd%e3%81%97%e3%81%bf%e3%81%9f%e3%81%84%e6%96%b9%e3%81%ab/",
    rent=67200, management_fee=8000, layout="1K", nearest_station="",
    train_line="名古屋市名城線", city="名古屋市 西エリア", walk_minutes=10,
    category=["新築", "ペット相談"], equipment=["オートロック", "独立洗面台"],
    is_vacant=True, is_commission_free=True, area_sqm=30.0, building_type="マンション",
)

INTRO = ("名古屋市名城線沿い、新築の1Kのお部屋です。オートロック・モニター付きドアホンで"
         "安心のセキュリティ環境が整い、独立洗面台やバストイレ別など毎日の生活を快適にする"
         "設備が充実しています。インターネット無料・仲介手数料0円もうれしいポイントです。"
         "ペット相談可能なので、大切な家族と一緒に新生活をスタートできますよ。")
INVITATION = "初期費用の目安やペット可の条件など、まとめてご相談いただけます。"


def main() -> None:
    cfg = load_config()
    sheets = SheetsClient(
        service_account_json=cfg["sheets"]["service_account_json"],
        spreadsheet_id=cfg["sheets"]["spreadsheet_id"],
        sheet_names=cfg["sheets"]["names"],
    )

    inq = Inquiry(
        id=str(uuid.uuid4()),
        received_at=datetime.now(),
        customer_name="テスト 太郎",
        customer_email=TEST_EMAIL,
        inquiry_property_name=PROP.name,
        inquiry_property_url=PROP.url,
        raw_body="「都市生活を楽しみたい方に」を内見希望です。初期費用も教えてください。",
        is_vacant=True,
        matched_property=PROP,
        status="自動送信可",
    )

    assembler = EmailAssembler(cfg["company"], is_business_hours=True)
    _, body_plain = assembler.build_first_mail_parts(inq, INTRO, INVITATION, [])
    inq.ai_draft = body_plain

    sheets.write_inquiry(inq)
    sheets.update_vacancy(inq.id, "あり")
    print(f"Seeded test inquiry {inq.id}")
    print(f"  customer email : {TEST_EMAIL}")
    print(f"  property       : {PROP.name}")
    print(f"  draft length   : {len(body_plain)} chars")
    print("Open the admin panel and review it under '自動送信可'.")


if __name__ == "__main__":
    main()
