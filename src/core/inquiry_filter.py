from __future__ import annotations
import logging
import re

logger = logging.getLogger(__name__)

# A rentmagazine.jp link or a 物件/内見/問い合わせ phrasing strongly signals a real inquiry.
_PROPERTY_SIGNAL = re.compile(
    r"rentmagazine\.jp|物件|内見|内覧|お問い合わせ|問い合わせ|空室|初期費用|来店|入居"
)
# Auto-generated bulk mail headers we should never treat as a customer inquiry.
_BULK_HEADER_HINTS = ("list-unsubscribe", "auto-submitted", "precedence")


class InquiryFilter:
    """
    Decides whether an incoming email is a genuine customer inquiry or a
    notification / portal / marketing mail that should be skipped.

    Skipping keeps notification mail out of the spreadsheet and avoids spending
    Claude credits drafting replies to robots.
    """

    def __init__(self, ignore_senders: list[str], enabled: bool = True,
                 require_property_signal: bool = False):
        self._enabled = enabled
        self._ignore = [s.lower() for s in (ignore_senders or [])]
        self._require_signal = require_property_signal

    def is_inquiry(self, raw: dict) -> bool:
        """Return True if this email should be processed as a customer inquiry."""
        if not self._enabled:
            return True

        # A reply to one of our sent mails is always relevant (handled upstream),
        # so never filter those out here.
        if raw.get("in_reply_to", "").strip():
            return True

        sender = (raw.get("from_addr") or "").lower()
        if any(frag in sender for frag in self._ignore):
            logger.info("Filtered out non-inquiry sender: %s", sender)
            return False

        # Bulk/automated mail headers (newsletters, notifications)
        headers = {k.lower() for k in (raw.get("headers_present") or [])}
        if headers & set(_BULK_HEADER_HINTS):
            logger.info("Filtered out bulk/automated mail from: %s", sender)
            return False

        has_signal = bool(_PROPERTY_SIGNAL.search(
            f"{raw.get('subject', '')} {raw.get('body', '')}"))
        if self._require_signal and not has_signal:
            # Strict mode: only process mail that references a property.
            logger.info("Filtered out (no property signal): %s", sender or "(no sender)")
            return False
        return True
