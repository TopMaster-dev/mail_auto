from __future__ import annotations
from datetime import datetime, time

from src.core.models import CheckResult, Inquiry

# Statuses that always block auto-send
_BLOCKED_STATUSES = {"要確認", "NG検出", "追客停止", "返信あり", "手動対応済み"}

# Inquiry types that MAY be auto-sent (when global switch is off and conditions match)
_AUTO_SEND_SAFE_TYPES = {
    "空室確認", "内見希望", "来店希望", "WEB面談希望",
    "資料請求", "初期費用概算", "類似物件紹介", "駅徒歩確認",
    "設備確認", "ペット可否確認", "駐車場確認", "入居可能日確認",
}

GATE_AUTO = "auto_send"
GATE_CONFIRM = "requires_confirmation"
GATE_BLOCKED = "blocked"


def is_business_hours() -> bool:
    now = datetime.now().time()
    return time(10, 0) <= now <= time(18, 0)


class SendGate:
    """
    Evaluates whether a draft may be auto-sent, requires human confirmation,
    or is blocked entirely.

    Default: all_require_confirmation = True (safe mode).
    Per-condition auto-send is enabled only when the global flag is False
    AND a matching condition key is True in Sheets config.
    """

    def __init__(self, global_require_confirmation: bool):
        self._global_confirm = global_require_confirmation

    def evaluate(
        self,
        inquiry: Inquiry,
        body_check: CheckResult,
        draft_check: CheckResult,
        auto_send_conditions: dict,
    ) -> str:
        """
        Returns one of: GATE_AUTO, GATE_CONFIRM, GATE_BLOCKED.
        Priority: BLOCKED > CONFIRM > AUTO.
        """
        # Hard block: safety checks failed
        if not body_check.is_clean or not draft_check.is_clean:
            return GATE_BLOCKED

        # Hard block: inquiry is already in a terminal/stopped state
        if inquiry.status in _BLOCKED_STATUSES:
            return GATE_BLOCKED

        # Hard block: customer has already replied (followup_status)
        if inquiry.followup_status in {"追客停止", "返信あり"}:
            return GATE_BLOCKED

        # Global safety switch overrides all per-condition settings
        if self._global_confirm:
            return GATE_CONFIRM

        # Per-condition auto-send: check if this inquiry type is whitelisted
        if auto_send_conditions.get("auto_send_all", False):
            return GATE_AUTO

        # Future: map inquiry type to a condition key and check
        # For now, conservatively default to CONFIRM
        return GATE_CONFIRM
