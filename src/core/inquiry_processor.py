from __future__ import annotations
import logging
import re
import uuid
from datetime import datetime

from src.ai.content_checker import ContentChecker
from src.ai.draft_generator import DraftGenerator
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
_PROP_NAME_PATTERN = re.compile(
    r"(?:物件名?|お問い合わせ物件|問い合わせ物件)[：:　\s]*([^\n\r]{2,40})"
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
    ):
        self._gmail = gmail
        self._sheets = sheets
        self._wp = wp
        self._checker = checker
        self._generator = generator
        self._scorer = scorer
        self._gate = gate
        self._company = company

    def run_cycle(self) -> None:
        """Called once per poll interval. Processes all unread emails."""
        logger.info("─── Poll cycle start ───")

        # Refresh NG word list from Sheets every cycle
        ng_words = self._sheets.read_ng_words()
        self._checker.reload_ng_words(ng_words)

        emails = self._gmail.fetch_unread()
        logger.info("Fetched %d unread email(s)", len(emails))

        auto_conditions = self._sheets.read_auto_send_conditions()

        for raw in emails:
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

        # ── Step 6: AI draft generation ──────────────────────────────────────
        try:
            draft, alt_intros = self._build_draft(inquiry)
        except RuntimeError as e:
            logger.error("Draft generation failed for %s: %s", inquiry.id, e)
            self._sheets.update_status(inquiry.id, "要確認")
            self._sheets.write_review_log(inquiry.id, "AI生成エラー", "")
            return

        inquiry.ai_draft = draft
        self._sheets.update_draft(inquiry.id, draft)

        # ── Step 7: check AI-generated draft ────────────────────────────────
        draft_check = self._checker.check(draft)
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
            in_hours = is_business_hours()
            assembler = EmailAssembler(self._company, in_hours)
            subject, html = assembler.build_first_mail(
                inquiry, draft, self._generator.generate_visit_invitation(inquiry.raw_body),
                alt_intros
            )
            try:
                sent_mid = self._gmail.send(inquiry.customer_email, subject, html,
                                            inquiry.message_id)
                inquiry.sent_at = datetime.now()
                inquiry.send_message_id = sent_mid
                self._sheets.mark_sent(inquiry.id, sent_mid)
                self._sheets.write_send_log(inquiry.id, inquiry.customer_email,
                                            subject, sent_mid, "1st")
                logger.info("Auto-sent 1st mail for inquiry %s", inquiry.id)
            except Exception as e:
                logger.error("SMTP send failed for %s: %s", inquiry.id, e)
                self._sheets.update_status(inquiry.id, "要確認")

    # ── helpers ──────────────────────────────────────────────────────────────

    def _parse_email(self, raw: dict) -> Inquiry:
        body = raw.get("body", "")
        name = raw.get("from_name") or raw.get("from_addr", "").split("@")[0]

        # Try to extract property name from body
        prop_name = ""
        m = _PROP_NAME_PATTERN.search(body)
        if m:
            prop_name = m.group(1).strip()

        # Try to extract property URL from body
        prop_url = ""
        urls = _PROP_URL_PATTERN.findall(body)
        if urls:
            prop_url = urls[0]

        # Fallback: use email subject
        if not prop_name:
            subj = raw.get("subject", "")
            bracket = re.search(r"[【\[](.+?)[】\]]", subj)
            prop_name = bracket.group(1) if bracket else subj

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

    def _lookup_property(self, inquiry: Inquiry) -> tuple[Property | None, bool]:
        prop = None
        if inquiry.inquiry_property_url:
            prop = self._wp.get_property_by_url(inquiry.inquiry_property_url)
        if prop is None and inquiry.inquiry_property_name:
            prop = self._wp.get_property_by_name(inquiry.inquiry_property_name)

        if prop is None:
            logger.warning("Could not match property for inquiry %s — treating as vacant=unknown",
                           inquiry.id)
            return None, True   # Default to vacant path (safer)

        return prop, prop.is_vacant

    def _build_draft(
        self, inquiry: Inquiry
    ) -> tuple[str, list[tuple[Property, str]]]:
        """
        Returns (draft_text_for_ng_check, alt_intros_list).
        draft_text is the full assembled mail text (plain) used for NG scanning.
        """
        alt_intros: list[tuple[Property, str]] = []

        if inquiry.is_vacant and inquiry.matched_property:
            prop_intro = self._generator.generate_property_intro(inquiry.matched_property)
            draft = prop_intro
        else:
            alts = self._scorer.find_alternatives(
                inquiry.matched_property or Property(
                    wp_id=0, name="", url="", rent=0, management_fee=0,
                    layout="", nearest_station="", train_line="", city="",
                    walk_minutes=0, category=[], equipment=[],
                    is_vacant=False, is_commission_free=False,
                    area_sqm=0.0, building_type="",
                ),
                top_n=3,
            )
            for alt in alts:
                alt_text = self._generator.generate_alt_intro(
                    inquiry.matched_property or alt, alt
                )
                alt_intros.append((alt, alt_text))
            draft = " ".join(text for _, text in alt_intros)

        return draft, alt_intros
