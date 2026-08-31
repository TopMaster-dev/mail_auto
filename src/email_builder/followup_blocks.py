"""
Fixed sections of the 2nd and 3rd follow-up mails.

Transcribed from the client's 「2nd・3rdメールテンプレート整理版」 (2026-05-18).
Everything here is fixed text or a config value — the AI writes only the
property introduction, exactly as their spec requires.

One deliberate departure: their example viewing prompt opens with
「大変人気のお部屋になっておりまして…」. That asserts demand for a specific
listing, and their own rules forbid stating inventory status unless it is known
to be true, so the neutral wording below is used instead.
"""
from __future__ import annotations

from datetime import date, timedelta

_WEEKDAYS = "月火水木金土日"

# Three concrete slots, since the template asks for proposed dates rather than a
# blank form. The office is open 10:00–18:00 every day, holidays included.
_SLOT_LABELS = ("午前（10:00〜12:00）", "午後（14:00〜16:00）", "午後（16:00〜18:00）")
_DAYS_AHEAD = (1, 2, 3)


def proposed_slots(today: date | None = None) -> list[str]:
    """`・第一希望　9月1日(月) 午前（10:00〜12:00）` × 3."""
    today = today or date.today()
    labels = ("第一希望", "第二希望", "第三希望")
    lines = []
    for label, ahead, slot in zip(labels, _DAYS_AHEAD, _SLOT_LABELS):
        d = today + timedelta(days=ahead)
        lines.append(f"・{label}　{d.month}月{d.day}日({_WEEKDAYS[d.weekday()]}) {slot}")
    return lines


VIEWING_PROMPT = """\
写真や条件だけでは分かりにくい広さや日当たり、周辺の雰囲気は、
実際にご覧いただくことでご比較いただきやすくなります。

下記の日程はご都合いかがでしょうか。"""

SCHEDULE_FALLBACK = """\
上記日程が合わない場合、{name}様のご都合のよいご希望日を
下記のテンプレートよりお知らせください。

・第一希望　月　日　午前　午後　時頃
・第二希望　月　日　午前　午後　時頃
・第三希望　月　日　午前　午後　時頃"""

MEETING = """\
物件現地での待ち合わせも可能です。
最寄駅までお迎えいたしますので、お気軽にお申し付けください。"""

PHONE_BOOKING = """\
お電話でのご予約の際は、{tel}までご連絡をお願いいたします。
［営業時間：10時〜18時］"""

COMMISSION_FREE = """\
ご案内・ご入居手続きを弊社でさせていただきましたら、
仲介手数料無料でご案内させていただいております。
この機会をお見逃しなく！"""

DISCOUNT = """\
▼個人割引：女性割引もあるのでぜひチェック✅
{url}"""

WEB_MEMBER = """\
WEB会員様限定で、仲介手数料無料の物件を発信中です。

弊社限定情報で初期費用を抑えやすい物件もあり、このところご登録者様が増えています。
いいお部屋から無くなっていきますので、早めの会員登録もおすすめです。

{url}"""

CONDITIONS = """\
また、ご予算やご希望のエリア、間取りなどをお知らせいただければ、
他にも物件をご紹介させていただきます。お気軽にお申し付けください。

【ご希望条件】
家　賃：〜　　　万円 →
間取り：　　　　　　 →
エリア：　　　　　　 →
最寄駅：　　　　　　 →
その他こだわり条件：
　例：ペット可、インターネット無料 等"""

LINE_INVITE = """\
★LINEからでも可能です♪
↓↓↓
{url}

友達追加で、お部屋情報をLINEにお届けします⭐"""

CLOSING = """\
ご質問やご不安な点がございましたら、何なりとご連絡ください。
ご検討よろしくお願いいたします。"""

# ── opening paragraphs ───────────────────────────────────────────────────────

OPENING_NORMAL = """\
先日はお問い合わせ誠にありがとうございます。
レントマガジン株式会社の{staff}と申します。"""

# Used when a call was attempted and not answered, selected from the operator's
# 担当者メモ via called_before_followup() below.
OPENING_AFTER_CALL = """\
先日はお問い合わせ頂きありがとうございました。
レントマガジン株式会社の{staff}と申します。

先ほどお電話にて「{property_name}」についてご紹介させていただきたく
ご連絡させていただきました。"""

SECOND_LEAD = """\
先日お問い合わせいただきました「{property_name}」について、
その後いかがでしょうか。

現在もご案内可能な場合は、ぜひ一度ご内覧いただければと思います。"""

THIRD_LEAD = """\
先日お問い合わせいただきました「{property_name}」について、
改めてご連絡させていただきました。

実際にご来店・ご内覧いただくお客様の中には、写真では分かりにくい広さや
周辺環境、日当たり、同条件の比較物件などを確認される方が多いです。
「まずは比較してみたい」という段階でも大丈夫ですので、
ご希望に近いお部屋をいくつかあわせてご案内させていただければと思います。"""

# In the after-call variant the opening has already named the property and given
# the reason for writing, so these drop that sentence and keep only the pitch.
SECOND_LEAD_AFTER_CALL = """\
現在もご案内可能な場合は、ぜひ一度ご内覧いただければと思います。"""

THIRD_LEAD_AFTER_CALL = """\
実際にご来店・ご内覧いただくお客様の中には、写真では分かりにくい広さや
周辺環境、日当たり、同条件の比較物件などを確認される方が多いです。
「まずは比較してみたい」という段階でも大丈夫ですので、
ご希望に近いお部屋をいくつかあわせてご案内させていただければと思います。"""

PROPERTY_BLOCK = """\
◆お問い合わせ物件
物件名：{name}

{url}

※上記URL内に初期費用を記載させていただきました"""

NEAREST_STATION = "【最寄駅】{line}「{station}」"


# ── after-call detection ────────────────────────────────────────────────────
# The client's template has a variant for "we phoned and you were out". Nothing
# in the mail flow can know a call was placed, so the operator records it in the
# 担当者メモ column (editable on the inquiry page) and the scheduler reads it.
# Kept to explicit markers: a memo merely mentioning 電話番号 must not trigger a
# mail claiming the customer was called.
_CALL_MARKERS = ("電話済", "架電済", "架電", "TEL済", "tel済", "電話不在", "不在")


def called_before_followup(memo: str) -> bool:
    """True when the operator has noted an outbound call on this inquiry."""
    text = str(memo or "")
    return any(marker in text for marker in _CALL_MARKERS)
