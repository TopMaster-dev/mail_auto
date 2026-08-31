from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

_JST = ZoneInfo("Asia/Tokyo")


def fmt_dt(dt: Optional[datetime]) -> str:
    """Format a timestamp for the spreadsheet, always in JST.

    Incoming mail carries the sender's own UTC offset — the WordPress form
    stamps -0400 — and strftime renders an aware datetime in *its* zone, which
    recorded receipts up to 16 hours early. Naive values are already
    server-local (the VPS runs Asia/Tokyo) and are left alone.
    """
    if dt is None:
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone(_JST)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


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
    # Formal building/room name from the `buildname` taxonomy, e.g. オリーブ_201.
    # `name` is the site's marketing title (「都市の利便性」); portals such as
    # SUUMO identify a property by this formal name instead.
    building_name: str = ""
    # Attribute matching relies on these because portals and the site write
    # building names in different scripts. Coverage differs sharply:
    # space 99%, layout 100%, access 99%, room 74%, rent 74%, address 29%.
    room_number: str = ""      # acf.floor, e.g. 205（2階部分）-> 205
    address: str = ""          # acf.location (sparse)
    access: str = ""           # acf.access, carries 沿線「駅」徒歩N分

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
    # A cost question on its own is routine, but the same words wrapped in
    # anger or dissatisfaction need a person. Judged by context, not keywords.
    complaint: bool = False
    complaint_reason: str = ""

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
            fmt_dt(self.received_at),
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
            fmt_dt(self.next_followup_at),
            fmt_dt(self.sent_at),
            self.followup_count,
            self.staff_memo,
            self.message_id,
        ]

    @classmethod
    def sheets_headers(cls) -> list[str]:
        return [
            "ID", "受信日時", "顧客名", "メールアドレス",
            "問い合わせ物件", "物件URL", "空室有無",
            "ステータス", "AI返信文案", "自動送信可否",
            "NGワード", "NGカテゴリ", "差別表現判定理由",
            "追客ステータス", "次回追客予定日", "送信日時",
            "追客回数", "担当者メモ", "受信メッセージID",
        ]

    @classmethod
    def from_sheets_record(cls, rec: dict) -> "Inquiry":
        """Reconstruct an Inquiry from a get_all_records() row (keys = headers).

        Used by the follow-up scheduler and the admin panel. Property objects are
        not restored here — callers re-resolve them from WordPress when needed.
        """
        def _dt(val):
            val = str(val or "").strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S"):
                try:
                    return datetime.strptime(val, fmt)
                except ValueError:
                    continue
            return None

        vac = str(rec.get("空室有無", "")).strip()
        is_vacant = True if vac == "あり" else (False if vac == "なし" else None)

        # Sheets hands back "1.0" for a numeric cell often enough that int() alone
        # would silently reset the counter to 0 and re-send an earlier follow-up.
        try:
            followup_count = int(float(str(rec.get("追客回数", 0) or 0)))
        except ValueError:
            followup_count = 0

        return cls(
            id=str(rec.get("ID", "")).strip(),
            received_at=_dt(rec.get("受信日時")) or datetime.now(),
            customer_name=str(rec.get("顧客名", "")).strip(),
            customer_email=str(rec.get("メールアドレス", "")).strip(),
            inquiry_property_name=str(rec.get("問い合わせ物件", "")).strip(),
            inquiry_property_url=str(rec.get("物件URL", "")).strip(),
            raw_body="",
            status=str(rec.get("ステータス", "")).strip(),
            is_vacant=is_vacant,
            ai_draft=str(rec.get("AI返信文案", "")),
            followup_status=str(rec.get("追客ステータス", "")).strip() or "追客中",
            next_followup_at=_dt(rec.get("次回追客予定日")),
            sent_at=_dt(rec.get("送信日時")),
            followup_count=followup_count,
            staff_memo=str(rec.get("担当者メモ", "")),
        )
