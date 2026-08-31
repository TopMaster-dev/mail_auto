from __future__ import annotations
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from src.core.models import Inquiry, Property
from src.email_builder import followup_blocks
from src.email_builder.assembler import EmailAssembler
from src.core.inquiry_processor import _INCLUDE_VISIT_INVITATION
from src.email_builder.send_gate import is_business_hours

logger = logging.getLogger(__name__)

_JOB_ID = "followup_scan"


def decide_followup(sent_count: int, steps_days: list[int], max_followups: int):
    """Pure decision: given how many mails were already sent, return what to do next.

    Returns (mail_type, new_count, next_days, status) or None if nothing to do.
    - sent_count == 1 → send the 2nd mail
    - sent_count == 2 → send the 3rd mail
    next_days is the interval until the *following* mail (None when finished).
    """
    if sent_count < 1 or sent_count > max_followups:
        return None
    mail_type = "2nd" if sent_count == 1 else f"{sent_count + 1}th"
    if sent_count == 2:
        mail_type = "3rd"
    new_count = sent_count + 1
    if new_count <= max_followups:
        # schedule the next follow-up; interval is the new_count-th step (1-indexed)
        next_days = steps_days[new_count - 1] if new_count - 1 < len(steps_days) else None
        status = "追客中"
    else:
        next_days = None
        status = "追客完了"
    return mail_type, new_count, next_days, status


class FollowupScheduler:
    """
    Sends scheduled follow-up mails (2nd / 3rd) to customers who haven't replied.

    A single recurring APScheduler job scans the spreadsheet every
    `scan_interval_minutes`; the spreadsheet is the source of truth for follow-up
    state (追客ステータス / 次回追客予定日 / 追客回数), so the scan is stateless and
    safely resumes after a restart (no local job DB needed).
    """

    def __init__(self, *, sheets, gmail, wp, generator, scorer, company: dict,
                 cfg: dict, checker=None):
        self._sheets = sheets
        self._gmail = gmail
        self._wp = wp
        self._generator = generator
        self._scorer = scorer
        self._company = company
        # ContentChecker. Optional so tests can build a bare scheduler, but
        # production must pass one — see start().
        self._checker = checker
        self._steps = [int(s["days"]) for s in cfg.get("steps", [])]
        self._max = int(cfg.get("max_followups", 2))
        self._interval = int(cfg.get("scan_interval_minutes", 30))
        self._scheduler: BackgroundScheduler | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._checker is None:
            logger.warning("Follow-up scheduler has no content checker — "
                           "follow-up mails will NOT be NG-screened")
        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(
            self.scan_and_send, "interval",
            minutes=self._interval, id=_JOB_ID, replace_existing=True,
            next_run_time=datetime.now(), coalesce=True, max_instances=1,
        )
        self._scheduler.start()
        logger.info("Follow-up scheduler started (every %d min)", self._interval)

    def shutdown(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            logger.info("Follow-up scheduler stopped")

    # ── scan ─────────────────────────────────────────────────────────────────

    def scan_and_send(self) -> None:
        if not is_business_hours():
            logger.debug("Outside business hours — skipping follow-up scan")
            return
        now = datetime.now()
        try:
            candidates = self._sheets.read_followup_candidates(now)
        except Exception as e:
            logger.exception("Follow-up scan failed to read candidates: %s", e)
            return
        if candidates:
            logger.info("Follow-up scan: %d candidate(s) due", len(candidates))
        for rec in candidates:
            try:
                self._send_followup(rec)
            except Exception as e:
                logger.exception("Follow-up send failed for %s: %s",
                                 rec.get("ID"), e)

    def _send_followup(self, rec: dict) -> None:
        inquiry = Inquiry.from_sheets_record(rec)

        decision = decide_followup(inquiry.followup_count, self._steps, self._max)
        if decision is None:
            # Already completed — make sure the sheet reflects that and stop.
            self._sheets.advance_followup(inquiry.id, inquiry.followup_count, None, "追客完了")
            return
        mail_type, new_count, next_days, status = decision

        if not inquiry.customer_email:
            logger.error("Inquiry %s has no address — stopping follow-up", inquiry.id)
            self._sheets.advance_followup(
                inquiry.id, inquiry.followup_count, None, "追客停止")
            return

        prop = self._resolve_property(inquiry)
        inquiry.matched_property = prop
        if inquiry.is_vacant is None and prop is not None:
            inquiry.is_vacant = prop.is_vacant

        intro, invitation, alt_intros = self._build_segments(inquiry, prop)
        ai_segments = [s for s in [intro, invitation,
                                   *(t for _, t in alt_intros)] if s and s.strip()]
        assembler = EmailAssembler(self._company, is_business_hours())
        build = (assembler.build_second_mail_parts if mail_type == "2nd"
                 else assembler.build_third_mail_parts)
        # 電話不在時パターン: only when the operator has recorded a call in 担当者メモ.
        after_call = followup_blocks.called_before_followup(inquiry.staff_memo)
        subject, body_plain = build(inquiry, intro, invitation, alt_intros,
                                    after_call)

        # Follow-up bodies are freshly generated AI text. They used to go out
        # without ever passing the NG / discriminatory-expression screen that
        # every first mail must clear.
        if self._checker is not None and not self._content_is_safe(
                inquiry, mail_type, ai_segments):
            return

        reply_to = inquiry.send_message_id or inquiry.message_id
        sent_mid = self._gmail.send(inquiry.customer_email, subject,
                                    EmailAssembler.wrap_html(body_plain), reply_to)

        # Log to send_log so a reply to this follow-up is also detected upstream.
        self._sheets.write_send_log(inquiry.id, inquiry.customer_email,
                                    subject, sent_mid, mail_type)
        next_at = datetime.now() + timedelta(days=next_days) if next_days else None
        self._sheets.advance_followup(inquiry.id, new_count, next_at, status)
        self._sheets.write_followup_log(inquiry.id, mail_type, "送信")
        logger.info("Sent %s follow-up for inquiry %s (status=%s)",
                    mail_type, inquiry.id, status)

    def _content_is_safe(self, inquiry: Inquiry, mail_type: str,
                         ai_segments: list[str]) -> bool:
        """Screen the AI-written parts. On a hit, record it and stop the sequence.

        Segments only, not the assembled mail: the fixed template contains
        初期費用 / 仲介手数料, which the client's word list also contains.
        """
        check = self._checker.check_generated(ai_segments)
        if check.is_clean:
            return True

        ng_str = ", ".join(h.word for h in check.ng_hits)
        self._sheets.update_status(inquiry.id, "NG検出")
        self._sheets.write_review_log(
            inquiry.id,
            f"{mail_type}追客メールNG: {check.discriminatory_reason}", ng_str)
        self._sheets.advance_followup(
            inquiry.id, inquiry.followup_count, None, "追客停止")
        self._sheets.write_followup_log(inquiry.id, mail_type, "NG検出のため送信中止")
        logger.warning("Follow-up %s for inquiry %s blocked by content check (%s)",
                       mail_type, inquiry.id, ng_str or check.discriminatory_reason)
        return False

    # ── content helpers (mirror InquiryProcessor's draft build) ──────────────

    def _resolve_property(self, inquiry: Inquiry) -> Property | None:
        if inquiry.inquiry_property_url:
            p = self._wp.get_property_by_url(inquiry.inquiry_property_url)
            if p:
                return p
        if inquiry.inquiry_property_name:
            return self._wp.get_property_by_name(inquiry.inquiry_property_name)
        return None

    def _build_segments(self, inquiry: Inquiry, prop: Property | None):
        seed = inquiry.raw_body or inquiry.inquiry_property_name or ""
        invitation = (self._generator.generate_visit_invitation(seed)
                      if _INCLUDE_VISIT_INVITATION else "")

        if inquiry.is_vacant and prop:
            intro = self._generator.generate_property_intro(prop)
            return intro, invitation, []

        base = prop or Property(
            wp_id=0, name="", url="", rent=0, management_fee=0, layout="",
            nearest_station="", train_line="", city="", walk_minutes=0,
            category=[], equipment=[], is_vacant=False, is_commission_free=False,
            area_sqm=0.0, building_type="",
        )
        alts = self._scorer.find_alternatives(base, top_n=3)
        alt_intros = [(a, self._generator.generate_alt_intro(prop or a, a)) for a in alts]
        return "", invitation, alt_intros
