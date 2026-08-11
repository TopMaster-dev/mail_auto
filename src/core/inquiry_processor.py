from __future__ import annotations
import logging
import re
import uuid
from datetime import datetime, timedelta

from src.ai.content_checker import ContentChecker
from src.ai.draft_generator import DraftGenerator
from src.core.inquiry_filter import InquiryFilter
from src.core.models import Inquiry, Property
from src.email_builder.assembler import EmailAssembler
from src.email_builder.send_gate import (
    GATE_AUTO, GATE_BLOCKED, GATE_CONFIRM, SendGate, is_business_hours
)
from src.integrations.gmail_client import GmailClient
from src.integrations.sheets_client import SheetsClient
from src.integrations.wp_client import WordPressClient
from src.matching.property_scorer import PropertyScorer

logger = logging.getLogger(__name__)

_PROP_URL_PATTERN = re.compile(r"https?://rentmagazine\.jp/\S+")
# Property name in Japanese/ASCII quotes — the most common way customers refer
# to a listing, e.g. 「都市生活を楽しみたい方に」の物件を内見希望です。
_PROP_QUOTED_PATTERN = re.compile(r"[「『\"”]([^「」『』\"”\n\r]{2,40})[」』\"”]")
# Labelled name with an explicit separator (portal/forwarded formats).
# Requires a real separator after the label so it can't grab "について" from "物件について".
_PROP_NAME_PATTERN = re.compile(
    r"(?:物件名|お問い合わせ物件|問い合わせ物件|ご希望物件)[：:　][\s]*([^\n\r「」]{2,40})"
)


class InquiryProcessor:
    """
    Orchestrates the full pipeline for one polling cycle:
    fetch → parse → NG check → property lookup → AI draft → NG check → send gate.
    """

    def __init__(
        self,
        gmail: GmailClient,
        sheets: SheetsClient,
        wp: WordPressClient,
        checker: ContentChecker,
        generator: DraftGenerator,
        scorer: PropertyScorer,
        gate: SendGate,
        company: dict,
        inquiry_filter: InquiryFilter | None = None,
        followup_cfg: dict | None = None,
    ):
        self._gmail = gmail
        self._sheets = sheets
        self._wp = wp
        self._checker = checker
        self._generator = generator
        self._scorer = scorer
        self._gate = gate
        self._company = company
        self._filter = inquiry_filter or InquiryFilter(ignore_senders=[], enabled=False)
        followup_cfg = followup_cfg or {}
        self._followup_enabled = followup_cfg.get("enabled", False)
        steps = followup_cfg.get("steps", [{"days": 2}])
        self._followup_first_days = int(steps[0]["days"]) if steps else 2

    def run_cycle(self) -> None:
        """Called once per poll interval. Processes all unread emails."""
        logger.info("─── Poll cycle start ───")

        # Refresh the NG word list first. Without it nothing can be screened, so
        # skip the whole cycle rather than process mail unchecked. Mail stays
        # unread because the IMAP fetch below never runs.
        try:
            self._checker.reload_ng_words(self._sheets.read_ng_words())
        except Exception as e:
            logger.error("Could not load NG words (%s) — skipping this cycle so "
                         "no mail is processed unscreened", e)
            return

        try:
            emails = self._gmail.fetch_unread()
        except Exception as e:
            logger.error("IMAP fetch failed — skipping this cycle: %s", e)
            return
        logger.info("Fetched %d unread email(s)", len(emails))

        auto_conditions = self._sheets.read_auto_send_conditions()

        for raw in emails:
            if not self._filter.is_inquiry(raw):
                continue
            try:
                self._process_one(raw, auto_conditions)
            except Exception as e:
                logger.exception("Unhandled error processing email uid=%s: %s",
                                 raw.get("uid"), e)

        logger.info("─── Poll cycle end ───")

    def _process_one(self, raw: dict, auto_conditions: dict) -> None:
        # ── Step 1: reply detection ──────────────────────────────────────────
        in_reply_to = raw.get("in_reply_to", "").strip()
        if in_reply_to:
            orig_id = self._sheets.find_inquiry_by_message_id(in_reply_to)
            if orig_id:
                self._sheets.update_status(orig_id, "返信あり")
                self._sheets.stop_followup(orig_id)
                logger.info("Customer replied to inquiry %s → followup stopped", orig_id)
                return

        # ── Step 2: parse inquiry ────────────────────────────────────────────
        inquiry = self._parse_email(raw)
        logger.info("Processing inquiry %s from %s", inquiry.id, inquiry.customer_email)

        # ── Step 3: write initial row ────────────────────────────────────────
        self._sheets.write_inquiry(inquiry)

        # No reply address means nothing downstream can succeed — bail out before
        # spending any Claude calls on a draft that could never be sent.
        if not inquiry.customer_email:
            logger.warning("Inquiry %s has no sender address → 要確認", inquiry.id)
            self._sheets.update_status(inquiry.id, "要確認")
            self._sheets.write_review_log(inquiry.id, "送信先メールアドレスなし", "")
            return

        # ── Step 4: check incoming email body ────────────────────────────────
        body_check = self._checker.check(inquiry.raw_body)
        if not body_check.is_clean:
            inquiry.ng_hits = body_check.ng_hits
            inquiry.discriminatory_flag = body_check.discriminatory
            inquiry.discriminatory_reason = body_check.discriminatory_reason
            inquiry.ng_category = body_check.ng_hits[0].category if body_check.ng_hits else ""
            self._sheets.update_status(inquiry.id, "NG検出")
            ng_str = ", ".join(h.word for h in body_check.ng_hits)
            reason = body_check.discriminatory_reason or ""
            self._sheets.write_review_log(inquiry.id,
                                          f"受信メールNG: {reason}", ng_str)
            logger.info("Inquiry %s → NG検出 (incoming body)", inquiry.id)
            return

        # ── Step 5: property lookup ──────────────────────────────────────────
        matched, is_vacant = self._lookup_property(inquiry)
        inquiry.matched_property = matched
        inquiry.is_vacant = is_vacant
        # Initial row was written before lookup — update 空室有無 once we know it
        self._sheets.update_vacancy(
            inquiry.id,
            "あり" if is_vacant else ("なし" if is_vacant is False else "不明"))

        # ── Step 6: AI draft generation → full assembled email body ──────────
        try:
            subject, body_plain, body_html = self._build_email(inquiry)
        except Exception as e:
            # Catch everything, not just the generator's RuntimeError: a scoring
            # or property-data failure here would otherwise fall through to the
            # caller's catch-all and leave the row silently stuck at 未対応.
            logger.exception("Draft build failed for %s: %s", inquiry.id, e)
            self._sheets.update_status(inquiry.id, "要確認")
            self._sheets.write_review_log(
                inquiry.id, f"文案生成エラー（{type(e).__name__}）", "")
            return

        inquiry.ai_draft = body_plain
        self._sheets.update_draft(inquiry.id, body_plain)

        # ── Step 7: check the assembled draft ────────────────────────────────
        draft_check = self._checker.check(body_plain)
        if not draft_check.is_clean:
            inquiry.discriminatory_flag = draft_check.discriminatory
            self._sheets.update_status(inquiry.id, "NG検出")
            ng_str = ", ".join(h.word for h in draft_check.ng_hits)
            self._sheets.write_review_log(inquiry.id,
                                          f"AI生成文NG: {draft_check.discriminatory_reason}",
                                          ng_str)
            logger.info("Inquiry %s → NG検出 (AI draft)", inquiry.id)
            return

        self._sheets.update_status(inquiry.id, "AI返信文生成済み")

        # ── Step 8: send gate ────────────────────────────────────────────────
        gate_result = self._gate.evaluate(inquiry, body_check, draft_check, auto_conditions)

        if gate_result == GATE_BLOCKED:
            self._sheets.update_status(inquiry.id, "要確認")
            logger.info("Inquiry %s → 要確認 (send gate blocked)", inquiry.id)

        elif gate_result == GATE_CONFIRM:
            self._sheets.update_status(inquiry.id, "自動送信可")
            logger.info("Inquiry %s → 自動送信可 (awaiting confirmation)", inquiry.id)

        elif gate_result == GATE_AUTO:
            try:
                sent_mid = self._gmail.send(inquiry.customer_email, subject, body_html,
                                            inquiry.message_id)
                inquiry.sent_at = datetime.now()
                inquiry.send_message_id = sent_mid
                self._sheets.mark_sent(inquiry.id, sent_mid)
                self._sheets.write_send_log(inquiry.id, inquiry.customer_email,
                                            subject, sent_mid, "1st")
                if self._followup_enabled:
                    next_at = datetime.now() + timedelta(days=self._followup_first_days)
                    self._sheets.schedule_followup(inquiry.id, next_at, count=1)
                logger.info("Auto-sent 1st mail for inquiry %s", inquiry.id)
            except Exception as e:
                logger.error("SMTP send failed for %s: %s", inquiry.id, e)
                self._sheets.update_status(inquiry.id, "要確認")

    # ── helpers ──────────────────────────────────────────────────────────────

    def _parse_email(self, raw: dict) -> Inquiry:
        body = raw.get("body", "")
        name = raw.get("from_name") or raw.get("from_addr", "").split("@")[0]

        # Try to extract property URL from body (most reliable identifier)
        prop_url = ""
        urls = _PROP_URL_PATTERN.findall(body)
        if urls:
            prop_url = urls[0]

        # Property name: quoted name first, then labelled name, then subject
        prop_name = ""
        m = _PROP_QUOTED_PATTERN.search(body)
        if m:
            prop_name = m.group(1).strip()
        if not prop_name:
            m = _PROP_NAME_PATTERN.search(body)
            if m:
                prop_name = m.group(1).strip()
        if not prop_name:
            subj = raw.get("subject", "")
            bracket = re.search(r"[【\[](.+?)[】\]]", subj)
            prop_name = (bracket.group(1) if bracket else subj).strip()

        return Inquiry(
            id=str(uuid.uuid4()),
            received_at=raw.get("date") or datetime.now(),
            customer_name=name,
            customer_email=raw.get("from_addr", ""),
            inquiry_property_name=prop_name,
            inquiry_property_url=prop_url,
            raw_body=body,
            message_id=raw.get("message_id", ""),
            in_reply_to=raw.get("in_reply_to", ""),
        )

    def _lookup_property(self, inquiry: Inquiry) -> tuple[Property | None, bool | None]:
        prop = None
        if inquiry.inquiry_property_url:
            prop = self._wp.get_property_by_url(inquiry.inquiry_property_url)
        if prop is None and inquiry.inquiry_property_name:
            prop = self._wp.get_property_by_name(inquiry.inquiry_property_name)

        if prop is None:
            # Vacancy is genuinely unknown. Claiming either way would put a false
            # statement in the mail, so leave it None and let the assembler use
            # its neutral wording.
            logger.warning("Could not match property for inquiry %s — vacancy unknown",
                           inquiry.id)
            return None, None

        return prop, prop.is_vacant

    def _build_email(self, inquiry: Inquiry) -> tuple[str, str, str]:
        """
        Generate AI segments and assemble the full first mail.
        Returns (subject, plain_body, html_body). The plain body is stored as the
        operator-reviewable draft and is what the NG check scans.
        """
        invitation = self._generator.generate_visit_invitation(inquiry.raw_body)
        alt_intros: list[tuple[Property, str]] = []

        if inquiry.is_vacant and inquiry.matched_property:
            intro = self._generator.generate_property_intro(inquiry.matched_property)
        else:
            intro = ""
            base = inquiry.matched_property or Property(
                wp_id=0, name="", url="", rent=0, management_fee=0,
                layout="", nearest_station="", train_line="", city="",
                walk_minutes=0, category=[], equipment=[],
                is_vacant=False, is_commission_free=False,
                area_sqm=0.0, building_type="",
            )
            for alt in self._scorer.find_alternatives(base, top_n=3):
                alt_text = self._generator.generate_alt_intro(
                    inquiry.matched_property or alt, alt)
                alt_intros.append((alt, alt_text))

        assembler = EmailAssembler(self._company, is_business_hours())
        subject, body_plain = assembler.build_first_mail_parts(
            inquiry, intro, invitation, alt_intros)
        return subject, body_plain, assembler.wrap_html(body_plain)
