from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Property:
    wp_id: int
    name: str
    url: str
    rent: int
    management_fee: int
    layout: str
    nearest_station: str
    train_line: str
    city: str
    walk_minutes: int
    category: list[str]
    equipment: list[str]
    is_vacant: bool
    is_commission_free: bool
    area_sqm: float
    building_type: str

    @property
    def rent_total(self) -> int:
        return self.rent + self.management_fee


@dataclass
class NGHit:
    word: str
    category: str


@dataclass
class CheckResult:
    is_clean: bool
    ng_hits: list[NGHit]
    discriminatory: bool
    discriminatory_reason: str

    @property
    def requires_review(self) -> bool:
        return not self.is_clean


@dataclass
class Inquiry:
    id: str
    received_at: datetime
    customer_name: str
    customer_email: str
    inquiry_property_name: str
    inquiry_property_url: str
    raw_body: str
    message_id: str = ""
    in_reply_to: str = ""
    status: str = "未対応"
    is_vacant: Optional[bool] = None
    matched_property: Optional[Property] = None
    alt_properties: list[Property] = field(default_factory=list)
    ai_draft: str = ""
    ai_draft_checked: bool = False
    auto_send_eligible: bool = False
    ng_hits: list[NGHit] = field(default_factory=list)
    ng_category: str = ""
    discriminatory_flag: bool = False
    discriminatory_reason: str = ""
    followup_status: str = "追客中"
    next_followup_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    send_message_id: str = ""
    followup_count: int = 0
    staff_memo: str = ""

    def to_sheets_row(self) -> list:
        """Googleスプレッドシートへの書き込み用フラットリスト"""
        ng_words_str = ", ".join(f"{h.word}({h.category})" for h in self.ng_hits)
        return [
            self.id,
            self.received_at.strftime("%Y-%m-%d %H:%M:%S"),
            self.customer_name,
            self.customer_email,
            self.inquiry_property_name,
            self.inquiry_property_url,
            "あり" if self.is_vacant else ("なし" if self.is_vacant is False else "不明"),
            self.status,
            self.ai_draft,
            "要確認" if self.discriminatory_flag or bool(self.ng_hits) else "自動送信可",
            ng_words_str,
            self.ng_category,
            self.discriminatory_reason,
            self.followup_status,
            self.next_followup_at.strftime("%Y-%m-%d %H:%M:%S") if self.next_followup_at else "",
            self.sent_at.strftime("%Y-%m-%d %H:%M:%S") if self.sent_at else "",
            self.followup_count,
            self.staff_memo,
        ]

    @classmethod
    def sheets_headers(cls) -> list[str]:
        return [
            "ID", "受信日時", "顧客名", "メールアドレス",
            "問い合わせ物件", "物件URL", "空室有無",
            "ステータス", "AI返信文案", "自動送信可否",
            "NGワード", "NGカテゴリ", "差別表現判定理由",
            "追客ステータス", "次回追客予定日", "送信日時",
            "追客回数", "担当者メモ",
        ]
