"""
Positive identification of reflection ("反響") mail.

The inbox receives far more than customer inquiries: vendor newsletters, portal
notifications, answering-service reports, and other WordPress form submissions
that share the same address. A denylist could not keep up — anything unknown was
treated as an inquiry, drafted a reply, and reached 自動送信可.

So identification is now positive: mail is an inquiry only when it matches a
known reflection format. Everything else is ignored outright.

Formats:
  webform  自社サイトのお問い合わせフォーム (contact@rentmagazine.jp 経由)
  suumo    SUUMO / リクルートJDS 反響お知らせメール

Both carry the customer's real name and address in the *body*; the envelope
sender is the form or the portal, never the customer.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Body text reaches us NFKC-normalised, so full-width colons and parentheses are
# already folded — but accept both forms in case a caller passes raw text.
_SEP = r"[:：]\s*"

# Other WordPress forms post to the same address. 解約 carries bank details,
# date of birth and a home address: never draft a reply to one.
_BLOCKED_FORM_SUBJECTS = (
    "解約", "退去", "更新", "求人", "採用", "オーナー", "査定", "買取",
)

_SUUMO_SENDERS = ("jds.suumo.jp", "suumo.jp")
_SUUMO_SUBJECT_HINTS = ("反響お知らせ", "ＪＤＳ", "JDS")


@dataclass
class Reflection:
    """A customer inquiry, normalised across source formats."""

    source: str                    # "webform" | "suumo"
    customer_name: str = ""
    customer_email: str = ""
    property_name: str = ""
    property_url: str = ""
    inquiry_text: str = ""         # what the customer actually asked, for the AI
    extras: dict = field(default_factory=dict)


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _field(body: str, *labels: str) -> str:
    """First non-empty value for any of the given labels."""
    for label in labels:
        m = re.search(rf"^[ \t　]*{re.escape(label)}{_SEP}(.*)$", body, re.MULTILINE)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return ""


def _split_name_and_url(value: str) -> tuple[str, str]:
    """`ガレージ付き戸建（https://…/）` -> ("ガレージ付き戸建", "https://…/")."""
    url = ""
    m = re.search(r"https?://\S+", value)
    if m:
        url = m.group(0).rstrip("）)　 ")
    name = re.sub(r"[（(]?\s*https?://\S*\s*[）)]?", "", value).strip()
    return name.strip("（）() 　"), url


def _looks_like_suumo(sender: str, subject: str) -> bool:
    if any(s in sender for s in _SUUMO_SENDERS):
        return True
    return any(h in subject for h in _SUUMO_SUBJECT_HINTS) and "反響" in subject


def _parse_suumo(body: str) -> Reflection:
    name = _field(body, "名前(漢字)", "名前（漢字）", "お名前", "名前")
    return Reflection(
        source="suumo",
        customer_name=name,
        customer_email=_field(body, "メールアドレス", "Email", "E-mail"),
        property_name=_field(body, "物件名"),
        property_url="",           # SUUMO links to suumo.jp; match by name instead
        inquiry_text=_field(body, "お問合せ内容", "お問い合わせ内容"),
        extras={
            "最寄り駅": _field(body, "最寄り駅"),
            "所在地": _field(body, "所在地"),
            "賃料": _field(body, "賃料"),
            "間取り": _field(body, "間取り"),
            "電話番号": _field(body, "TEL", "ＴＥＬ", "電話番号"),
        },
    )


def _parse_webform(body: str) -> Reflection:
    prop_name, prop_url = _split_name_and_url(_field(body, "お問い合わせ物件", "問い合わせ物件"))
    if not prop_url:
        m = re.search(r"https?://rentmagazine\.jp/\S+", body)
        prop_url = m.group(0) if m else ""
    wants = [
        _field(body, "お問い合わせ内容", "お問合せ内容"),
        _field(body, "希望条件"),
        _field(body, "お引越し時期"),
        _field(body, "お引越し人数"),
    ]
    return Reflection(
        source="webform",
        customer_name=_field(body, "お名前", "氏名"),
        customer_email=_field(body, "メールアドレス"),
        property_name=prop_name,
        property_url=prop_url,
        inquiry_text="\n".join(w for w in wants if w),
        extras={"電話番号": _field(body, "お電話番号", "電話番号")},
    )


def identify(raw: dict) -> Reflection | None:
    """Return a Reflection when this mail is a customer inquiry, else None."""
    sender = (raw.get("from_addr") or "").lower()
    subject = _norm(raw.get("subject") or "")
    body = _norm(raw.get("body") or "")

    if _looks_like_suumo(sender, subject):
        parsed = _parse_suumo(body)
        # A JDS mail with no customer block is a notification, not a reflection.
        return parsed if (parsed.customer_email or parsed.property_name) else None

    # Own-site forms. 「お問い合わせ物件」 is what separates an inquiry from the
    # 解約 / 更新 forms that post to the same address.
    if "お問い合わせ物件" in body or "問い合わせ物件" in body:
        if any(word in subject for word in _BLOCKED_FORM_SUBJECTS):
            return None
        parsed = _parse_webform(body)
        return parsed if parsed.customer_email else None

    return None
