from __future__ import annotations
import logging
import smtplib
import time
import unicodedata
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Generator

from imap_tools import MailBox, AND, MailMessage

logger = logging.getLogger(__name__)

# How far back each poll looks. Ingestion is keyed on Message-ID rather than on
# the unread flag, so a mail someone opened first is still picked up; this
# window only bounds how much of the mailbox is re-scanned each cycle.
_LOOKBACK_DAYS = 7


class GmailClient:
    def __init__(self, address: str, app_password: str,
                 imap_host: str, smtp_host: str, smtp_port: int):
        self._address = address
        self._password = app_password
        self._imap_host = imap_host
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port

    # ── receive ─────────────────────────────────────────────────────────────

    def fetch_recent(self) -> list[dict]:
        """
        Connect → fetch the last _LOOKBACK_DAYS of mail → disconnect.
        Returns normalised dicts ready for InquiryProcessor.
        Retries up to 3 times with exponential backoff on connection errors.
        """
        for attempt in range(3):
            try:
                return self._do_fetch()
            except Exception as e:
                wait = 10 * (2 ** attempt)
                logger.warning("IMAP fetch attempt %d failed: %s — retry in %ds", attempt + 1, e, wait)
                if attempt == 2:
                    raise
                time.sleep(wait)
        return []

    def _do_fetch(self) -> list[dict]:
        results: list[dict] = []
        since = date.today() - timedelta(days=_LOOKBACK_DAYS)
        with MailBox(self._imap_host).login(self._address, self._password) as mb:
            # Deliberately not filtered on seen=False, and deliberately not
            # marking anything seen. Reflections were being lost outright: staff
            # opened a 反響 in the mailbox before the poller ran, seen=False then
            # excluded it, and it could never be ingested afterwards. Marking
            # seen was unsafe on its own too — the batch is flagged before any
            # row is written, so an error mid-cycle discarded the mail for good.
            # Duplicate protection is the Message-ID check in InquiryProcessor,
            # which survives both cases.
            for msg in mb.fetch(AND(date_gte=since), mark_seen=False, bulk=True):
                try:
                    results.append(self._parse(msg))
                except Exception as e:
                    # One malformed message must not abort the whole batch.
                    logger.error("Failed to parse message uid=%s: %s", msg.uid, e)
        logger.info("Fetched %d email(s) since %s", len(results), since)
        return results

    def _parse(self, msg: MailMessage) -> dict:
        body = msg.text or ""
        if not body and msg.html:
            body = msg.html
        from_name = (msg.from_values.name if msg.from_values else "") or ""
        return {
            "uid": msg.uid,
            "message_id": (msg.headers.get("message-id") or [""])[0].strip(),
            "in_reply_to": (msg.headers.get("in-reply-to") or [""])[0].strip(),
            "from_addr": msg.from_ or "",
            "from_name": from_name.strip(),
            "subject": msg.subject or "",
            "body": unicodedata.normalize("NFKC", body),
            "date": msg.date,
            "headers_present": list((msg.headers or {}).keys()),
        }

    # ── send ─────────────────────────────────────────────────────────────────

    def send(self, to: str, subject: str, body_html: str,
             reply_to_message_id: str = "") -> str:
        """
        Send an HTML email via Gmail SMTP.
        Returns the generated Message-ID.
        Raises on failure (caller decides whether to mark as 要確認).
        """
        msg = MIMEMultipart("alternative")
        msg["From"] = self._address
        msg["To"] = to
        msg["Subject"] = subject
        if reply_to_message_id:
            msg["In-Reply-To"] = reply_to_message_id
            msg["References"] = reply_to_message_id

        msg.attach(MIMEText(body_html, "html", "utf-8"))
        message_id = msg["Message-ID"] or f"<mail_auto_{int(time.time())}@rentmagazine.jp>"
        msg["Message-ID"] = message_id

        for attempt in range(3):
            try:
                with smtplib.SMTP(self._smtp_host, self._smtp_port) as s:
                    s.ehlo()
                    s.starttls()
                    s.login(self._address, self._password)
                    s.sendmail(self._address, [to], msg.as_bytes())
                logger.info("Sent email to %s (subject: %s)", to, subject)
                return message_id
            except smtplib.SMTPException as e:
                wait = 5 * (2 ** attempt)
                logger.warning("SMTP attempt %d failed: %s — retry in %ds", attempt + 1, e, wait)
                if attempt == 2:
                    raise
                time.sleep(wait)
        return message_id
