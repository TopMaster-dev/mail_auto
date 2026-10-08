"""
Operator-editable mail templates, read from the テンプレート sheet.

The client edits the 1st/2nd/3rd subject and body themselves; the system fills
the placeholders. Three rules shape this module:

1. An unset template is not an error. A blank row means "use the built-in text",
   so the feature can ship against an empty sheet without stopping any mail.

2. A set-but-broken template must never fall back silently. Quietly sending the
   previous wording would leave the client believing their edit was live while
   customers received something else, with no way to notice (client, 2026-10-07).
   Broken means stop, loudly.

3. Recovery needs no per-inquiry cleanup. Because sending stops *before* anything
   is recorded or sent, fixing the sheet is the whole repair — the next cycle
   proceeds normally. Nothing has to be re-queued by hand.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

FIRST, SECOND, THIRD = "1st", "2nd", "3rd"
KINDS = (FIRST, SECOND, THIRD)

# Row names in the テンプレート sheet, per mail.
SUBJECT_ROW = {FIRST: "1stメール_件名", SECOND: "2ndメール_件名", THIRD: "3rdメール_件名"}
BODY_ROW = {FIRST: "1stメール_本文", SECOND: "2ndメール_本文", THIRD: "3rdメール_本文"}

# What the operator may write. Anything else is a mistake, not a literal: a stray
# 「{物件名称}」 would otherwise reach a customer verbatim.
PLACEHOLDERS: dict[str, str] = {
    "顧客名": "お客様のお名前（「様」は自動では付きません）",
    "物件名": "お問い合わせ物件名",
    "物件URL": "物件ページのURL",
    "AI紹介文": "AIが生成する物件紹介文",
    # One placeholder for the whole property section, because its shape depends
    # on data the operator cannot see when editing: an available room becomes
    # 「◆お問い合わせ物件」, an unavailable one becomes the 「◆おすすめ物件」 list.
    "物件情報": "物件情報のまとまり（空室の場合はお問い合わせ物件、"
                "空室でない場合はおすすめ物件一覧に自動で切り替わります）",
    "最寄駅": "最寄駅（沿線名を含む）",
    "担当者名": "担当者名",
    "署名": "会社署名（住所・TEL等）",
}

_TOKEN = re.compile(r"\{([^{}]*)\}")


@dataclass
class Template:
    kind: str
    subject: str
    body: str
    errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @property
    def is_configured(self) -> bool:
        return bool(self.subject.strip() or self.body.strip())


# Full-width braces look identical enough to be typed by accident, and would
# otherwise pass unnoticed and reach a customer as literal 「｛物件名｝」.
_FULLWIDTH_BRACES = ("｛", "｝")


def _norm(text: str) -> str:
    """For comparison only. Never apply this to text that will be sent: NFKC
    rewrites the operator's typography — 「物件名：」 would go out as 「物件名:」."""
    return unicodedata.normalize("NFKC", str(text or ""))


def placeholder_help() -> list[list[str]]:
    """Rows describing every usable placeholder, written into the sheet itself."""
    return [[f"{{{name}}}", desc] for name, desc in PLACEHOLDERS.items()]


def validate(subject: str, body: str, kind: str = "") -> list[str]:
    """Everything wrong with one template, in Japanese, for the operator."""
    errors: list[str] = []
    label = f"{kind}メール" if kind else "テンプレート"

    if not str(body or "").strip():
        errors.append(f"{label}の本文が空です。")
    if not str(subject or "").strip():
        errors.append(f"{label}の件名が空です。")

    for where, text in (("件名", subject), ("本文", body)):
        text = str(text or "")      # raw: validation must judge what is sent
        if any(b in text for b in _FULLWIDTH_BRACES):
            errors.append(
                f"{label}の{where}に全角の｛｝が使われています。"
                f"差し込み項目は半角の {{ }} で記述してください。")
        # Unbalanced braces: count them rather than trusting the token regex,
        # which simply will not match 「{物件名」 and would let it through.
        if text.count("{") != text.count("}"):
            errors.append(
                f"{label}の{where}で、差し込み項目の括弧 {{ }} の数が合っていません。")
        for name in _TOKEN.findall(text):
            key = name.strip()
            if not key:
                errors.append(f"{label}の{where}に、中身が空の差し込み項目 {{}} があります。")
            elif key not in PLACEHOLDERS:
                allowed = "、".join(f"{{{k}}}" for k in PLACEHOLDERS)
                errors.append(
                    f"{label}の{where}の差し込み項目 {{{key}}} は使用できません。"
                    f"使用可能な項目：{allowed}")
    return errors


def render(text: str, context: dict[str, str]) -> str:
    """Substitute placeholders. Only validated templates should reach here.

    An unknown token is left untouched rather than raising: validation has
    already refused to send such a template, so reaching this point means the
    caller bypassed the gate, and losing the surrounding text would be worse.
    """
    def _sub(m: re.Match) -> str:
        key = m.group(1).strip()
        if key in context:
            return str(context[key] or "")
        if key in PLACEHOLDERS:
            return ""
        return m.group(0)

    # Raw text, deliberately not normalised — see _norm.
    return _TOKEN.sub(_sub, str(text or ""))


class TemplateStore:
    """Reads the テンプレート sheet and reports what may be used.

    Held in memory between refreshes; the caller decides how often to refresh
    (every poll cycle, so an edit is picked up within one interval).
    """

    def __init__(self, sheets, checker=None):
        self._sheets = sheets
        self._checker = checker
        self._templates: dict[str, Template] = {}
        self._read_error: str = ""

    # ── loading ─────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        try:
            rows = self._sheets.read_templates()
            self._read_error = ""
        except Exception as e:
            # Keep whatever was loaded before and say so. An unreadable sheet
            # must not silently look like "nothing configured", which would
            # quietly revert the client to the built-in wording.
            self._read_error = f"テンプレートシートを読み取れません：{e}"
            logger.error("Could not read the テンプレート sheet: %s", e)
            return

        values = {str(r.get("テンプレート名", "")).strip(): str(r.get("内容", ""))
                  for r in rows}
        loaded: dict[str, Template] = {}
        for kind in KINDS:
            subject = values.get(SUBJECT_ROW[kind], "")
            body = values.get(BODY_ROW[kind], "")
            tpl = Template(kind=kind, subject=subject, body=body)
            if tpl.is_configured:
                tpl.errors = validate(subject, body, kind)
                tpl.errors += self._content_errors(tpl)
            loaded[kind] = tpl
        self._templates = loaded

        broken = [k for k, t in loaded.items() if t.is_configured and not t.is_valid]
        if broken:
            logger.warning("Templates with errors: %s", ", ".join(broken))

    def _content_errors(self, tpl: Template) -> list[str]:
        """Screen the operator's own wording for NG words and discriminatory text."""
        if self._checker is None:
            return []
        try:
            result = self._checker.check(f"{tpl.subject}\n{tpl.body}")
        except Exception as e:
            logger.error("Could not screen template %s: %s", tpl.kind, e)
            return []
        if result.is_clean:
            return []
        reasons = []
        if result.ng_hits:
            reasons.append("NGワード：" + "、".join(h.word for h in result.ng_hits))
        if getattr(result, "discriminatory", False):
            reasons.append(f"差別的表現の可能性：{result.discriminatory_reason}")
        return [f"{tpl.kind}メールの文面に問題が検出されました（{' / '.join(reasons)}）。"]

    # ── querying ────────────────────────────────────────────────────────────

    def usable(self, kind: str) -> Template | None:
        """The template to use, or None to fall back to the built-in text.

        None means *not configured*, never *broken* — callers must check
        `blocking_errors` first and refuse to send while anything is broken.
        """
        tpl = self._templates.get(kind)
        if tpl is None or not tpl.is_configured or not tpl.is_valid:
            return None
        return tpl

    def blocking_errors(self, kind: str) -> list[str]:
        """Why this mail must not be sent. Empty when it may go out."""
        if self._read_error:
            return [self._read_error]
        tpl = self._templates.get(kind)
        if tpl is None or not tpl.is_configured:
            return []
        return list(tpl.errors)

    def all_errors(self) -> dict[str, list[str]]:
        """Every problem across all three mails, for the dashboard banner."""
        if self._read_error:
            return {"テンプレート": [self._read_error]}
        return {k: list(t.errors) for k, t in self._templates.items()
                if t.is_configured and t.errors}
