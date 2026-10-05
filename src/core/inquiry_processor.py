from __future__ import annotations
import logging
import re
import uuid
from datetime import datetime, timedelta

from src.ai.content_checker import ContentChecker
from src.ai.draft_generator import DraftGenerator
from src.core.models import Inquiry, Property
from src.core.reflection import Reflection, identify
from src.email_builder.assembler import EmailAssembler
from src.email_builder.send_gate import (
    GATE_AUTO, GATE_BLOCKED, GATE_CONFIRM, SendGate, is_business_hours
)
from src.integrations.gmail_client import GmailClient
from src.integrations.sheets_client import SheetsClient
from src.integrations.wp_client import WordPressClient
from src.matching import area, portal_matcher
from src.matching.property_scorer import PropertyScorer

logger = logging.getLogger(__name__)

# imap_tools yields a 1900-01-01 sentinel when the Date header is missing or
# unparseable. It is truthy, so it used to be stored verbatim.
_EARLIEST_PLAUSIBLE_YEAR = 2000

# 問い合わせ物件 column, rewritten when a portal name resolves to a building.
_PROPERTY_NAME_COL = 5

# The AI-written 来店誘導前の一言 (spec 7-3) was withdrawn on the client's
# instruction: the generated sentence read unnaturally and they asked for it
# to be dropped outright rather than replaced. Set True to reinstate.
_INCLUDE_VISIT_INVITATION = False


def _received_at(raw: dict) -> datetime:
    """The message's Date, or now when it is absent or implausible."""
    dt = raw.get("date")
    if not isinstance(dt, datetime) or dt.year < _EARLIEST_PLAUSIBLE_YEAR:
        return datetime.now()
    return dt


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
        followup_cfg: dict | None = None,
        wp_refresh_minutes: int = 30,
    ):
        self._gmail = gmail
        self._sheets = sheets
        self._wp = wp
        self._checker = checker
        self._generator = generator
        self._scorer = scorer
        self._gate = gate
        self._company = company
        self._wp_refresh_minutes = int(wp_refresh_minutes)
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

        self._refresh_listings()
        self._sweep_interrupted()

        try:
            emails = self._gmail.fetch_recent()
        except Exception as e:
            logger.error("IMAP fetch failed — skipping this cycle: %s", e)
            return
        logger.info("Fetched %d email(s) to consider", len(emails))

        auto_conditions = self._sheets.read_auto_send_conditions()
        try:
            seen_ids = self._sheets.read_seen_message_ids()
        except Exception as e:
            # Without the set we cannot dedup; process anyway rather than
            # drop real inquiries, but say so.
            logger.error("Could not read recorded Message-IDs (%s) — "
                         "duplicate protection is off this cycle", e)
            seen_ids = set()

        for raw in emails:
            try:
                self._process_one(raw, auto_conditions, seen_ids)
            except Exception as e:
                logger.exception("Unhandled error processing email uid=%s: %s",
                                 raw.get("uid"), e)

        logger.info("─── Poll cycle end ───")

    def _refresh_listings(self) -> None:
        """Re-read WordPress when the cached listings have aged out.

        The scorer is rebuilt in step: it keeps its own DataFrame, so refreshing
        only the client would leave alternative suggestions drawn from the
        startup snapshot.
        """
        try:
            if self._wp.refresh_if_stale(self._wp_refresh_minutes):
                self._scorer.reload(self._wp.properties)
        except Exception as e:
            # Never abort a cycle over this — the previous snapshot is still
            # serviceable, and mail waiting to be read matters more.
            logger.exception("Listing refresh failed: %s", e)

    def _sweep_interrupted(self) -> None:
        """Flag rows left mid-processing by a crash or restart as 処理中断.

        処理中 is written for a second or two between drafting and the send gate,
        and rows are processed one at a time in this process — so nothing is
        legitimately 処理中 when a new cycle begins. Anything still there was
        interrupted, and would otherwise sit unnoticed and unsent forever.
        """
        try:
            rows = self._sheets.read_inquiries()
        except Exception as e:
            logger.error("Could not scan for interrupted rows: %s", e)
            return
        for rec in rows:
            if str(rec.get("ステータス", "")).strip() != "処理中":
                continue
            iid = str(rec.get("ID", "")).strip()
            if not iid:
                continue
            logger.warning("Inquiry %s was left mid-processing → 処理中断", iid)
            self._sheets.update_status(iid, "処理中断")
            self._sheets.write_review_log(
                iid, "前回の処理が中断されました（要手動確認）", "")

    def _already_marked_replied(self, inquiry_id: str) -> bool:
        """Has this inquiry already been recorded as replied-to?"""
        try:
            rec = self._sheets.get_inquiry(inquiry_id) or {}
        except Exception as e:
            # Unknown: treat as not yet marked. Re-recording a reply is harmless;
            # missing one would keep chasing a customer who already answered.
            logger.warning("Could not read inquiry %s: %s", inquiry_id, e)
            return False
        return (str(rec.get("ステータス", "")).strip() == "返信あり"
                and str(rec.get("追客ステータス", "")).strip() == "追客停止")

    def _process_one(self, raw: dict, auto_conditions: dict,
                     seen_ids: set[str] | None = None) -> None:
        # ── Step 1: reply detection ──────────────────────────────────────────
        in_reply_to = raw.get("in_reply_to", "").strip()
        if in_reply_to:
            orig_id = self._sheets.find_inquiry_by_message_id(in_reply_to)
            if orig_id:
                # Always stop here — this mail is a reply, never a new inquiry.
                # Acting is guarded separately: the mailbox is now re-scanned in
                # full every cycle, so the same reply is seen over and over and
                # would otherwise rewrite the row and re-log every few minutes.
                if not self._already_marked_replied(orig_id):
                    self._sheets.update_status(orig_id, "返信あり")
                    self._sheets.stop_followup(orig_id)
                    logger.info("Customer replied to inquiry %s → followup stopped",
                                orig_id)
                return

        # ── Step 2: is this a reflection at all? ─────────────────────────────
        # Positive identification. Anything that is not a recognised reflection
        # format (vendor mail, portal notices, the 解約 form) is ignored outright
        # rather than drafted a reply and surfaced as 自動送信可.
        reflection = identify(raw)
        if reflection is None:
            logger.info("Not a reflection — skipped: from=%s subject=%s",
                        raw.get("from_addr"), (raw.get("subject") or "")[:60])
            return

        # Already recorded? Re-reading the same mail must not create a second
        # row — sending both would mail the customer the identical reply twice.
        message_id = (raw.get("message_id") or "").strip()
        if message_id and seen_ids and message_id in seen_ids:
            logger.info("Already recorded, skipping: %s (%s)",
                        message_id, (raw.get("subject") or "")[:40])
            return

        inquiry = self._build_inquiry(raw, reflection)
        logger.info("Processing inquiry %s from %s", inquiry.id, inquiry.customer_email)
        if message_id and seen_ids is not None:
            seen_ids.add(message_id)

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
            reason = " / ".join(r for r in (body_check.discriminatory_reason,
                                            body_check.complaint_reason) if r)
            self._sheets.update_ng_result(inquiry.id, ng_str,
                                          inquiry.ng_category, reason)
            self._sheets.write_review_log(inquiry.id,
                                          f"受信メールNG: {reason}", ng_str)
            logger.info("Inquiry %s → NG検出 (incoming body)", inquiry.id)
            return

        # ── Step 5: property lookup ──────────────────────────────────────────
        matched, is_vacant = self._lookup_property(inquiry, reflection)
        inquiry.matched_property = matched
        inquiry.is_vacant = is_vacant
        # Initial row was written before lookup — update 空室有無 once we know it
        self._sheets.update_vacancy(
            inquiry.id,
            "あり" if is_vacant else ("なし" if is_vacant is False else "不明"))

        # ── Step 6: AI draft generation → full assembled email body ──────────
        try:
            subject, body_plain, body_html, ai_segments = self._build_email(
                inquiry, reflection)
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

        # ── Step 7: check the AI-written text ────────────────────────────────
        # Only the generated segments — not the whole assembled mail. The fixed
        # template the client supplied itself contains 初期費用 and 仲介手数料,
        # so scanning the full body against their word list would flag every
        # draft forever and nothing could ever be sent. Spec 17-2 asks for the
        # AI-generated reply text to be screened, which is exactly this.
        draft_check = self._checker.check_generated(ai_segments)
        if not draft_check.is_clean:
            inquiry.discriminatory_flag = draft_check.discriminatory
            self._sheets.update_status(inquiry.id, "NG検出")
            ng_str = ", ".join(h.word for h in draft_check.ng_hits)
            category = draft_check.ng_hits[0].category if draft_check.ng_hits else ""
            reason = " / ".join(r for r in (draft_check.discriminatory_reason,
                                            draft_check.complaint_reason) if r)
            self._sheets.update_ng_result(
                inquiry.id, ng_str, category, f"AI生成文: {reason}" if reason else "")
            self._sheets.write_review_log(inquiry.id,
                                          f"AI生成文NG: {draft_check.discriminatory_reason}",
                                          ng_str)
            logger.info("Inquiry %s → NG検出 (AI draft)", inquiry.id)
            return

        # Transient: the send gate below replaces this within the same cycle. A
        # row still carrying it when the next cycle starts means processing was
        # interrupted, which _sweep_interrupted() turns into 処理中断.
        self._sheets.update_status(inquiry.id, "処理中")

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
                                            subject, sent_mid, "1st", body_plain)
                if self._followup_enabled:
                    next_at = datetime.now() + timedelta(days=self._followup_first_days)
                    self._sheets.schedule_followup(inquiry.id, next_at, count=1)
                logger.info("Auto-sent 1st mail for inquiry %s", inquiry.id)
            except Exception as e:
                logger.error("SMTP send failed for %s: %s", inquiry.id, e)
                self._sheets.update_status(inquiry.id, "要確認")

    # ── helpers ──────────────────────────────────────────────────────────────

    def _build_inquiry(self, raw: dict, reflection: Reflection) -> Inquiry:
        """Build an Inquiry from an identified reflection.

        Name and address come from the message *body*. The envelope sender is
        the web form (contact@rentmagazine.jp) or the portal (jds.suumo.jp) —
        never the customer — so replying to it sent mail back to the company
        instead of to the person who enquired.

        `raw_body` stays the full message so the NG scan sees everything.
        """
        return Inquiry(
            id=str(uuid.uuid4()),
            received_at=_received_at(raw),
            customer_name=reflection.customer_name,
            customer_email=reflection.customer_email,
            inquiry_property_name=reflection.property_name,
            inquiry_property_url=reflection.property_url,
            raw_body=raw.get("body", ""),
            message_id=raw.get("message_id", ""),
            in_reply_to=raw.get("in_reply_to", ""),
        )

    @staticmethod
    def _reference_property(reflection: Reflection | None) -> Property:
        """A stand-in reference when the enquiry property isn't in our listings.

        A blank reference makes the alternatives ranking near-arbitrary, which is
        how a 岡崎市 enquiry came back with 名古屋市 suggestions. Portal mail
        carries the address in its own body, so at least the area is known and
        the area guard can keep the suggestions local.
        """
        city = ""
        if reflection is not None:
            city = area.city_from_address(reflection.extras.get("所在地", ""))
        return Property(
            wp_id=0, name="", url="", rent=0, management_fee=0,
            layout="", nearest_station="", train_line="", city=city,
            walk_minutes=0, category=[], equipment=[],
            is_vacant=False, is_commission_free=False,
            area_sqm=0.0, building_type="",
        )

    def _lookup_property(self, inquiry: Inquiry,
                         reflection: Reflection | None = None
                         ) -> tuple[Property | None, bool | None]:
        """Find the listing the customer asked about.

        Portal mail carries no link to our site — it identifies a property by
        its formal name (サンステージエクセル203), which lives in the buildname
        taxonomy rather than the post title. When only the building matches,
        the room number is dropped from the display name so the reply
        introduces the building without naming a room we cannot confirm.
        """
        prop = None
        if inquiry.inquiry_property_url:
            prop = self._wp.get_property_by_url(inquiry.inquiry_property_url)

        display = ""
        if prop is None and inquiry.inquiry_property_name:
            prop, display = self._wp.resolve_by_formal_name(inquiry.inquiry_property_name)

        # Name matching only works when both sides use the same script. Roughly
        # half the site's building names are romaji or mixed while portals send
        # katakana, so fall back to the attributes the reflection carries.
        if prop is None and reflection is not None:
            found = portal_matcher.match(reflection, self._wp.properties)
            if found is not None:
                prop, display = found.prop, found.display_name
                logger.info("Inquiry %s matched by attributes (%s)",
                            inquiry.id, found.basis)

        if prop is not None:
            if display and display != inquiry.inquiry_property_name:
                logger.info("Display name adjusted: %s → %s",
                            inquiry.inquiry_property_name, display)
                inquiry.inquiry_property_name = display
            self._sheets.update_inquiry_field(
                inquiry.id, _PROPERTY_NAME_COL, inquiry.inquiry_property_name)

        if prop is None and inquiry.inquiry_property_name:
            prop = self._wp.get_property_by_name(inquiry.inquiry_property_name)

        if prop is None:
            # Vacancy is genuinely unknown. Claiming either way would put a false
            # statement in the mail, so leave it None and let the assembler use
            # its neutral wording.
            logger.warning("Could not match property for inquiry %s — vacancy unknown",
                           inquiry.id)
            return None, None

        # Confirm vacancy against WordPress right now rather than against the
        # snapshot. This is the one listing the mail asserts something about, so
        # a stale answer here is the error the customer actually reads.
        fresh = self._wp.refetch_one(prop.wp_id)
        if fresh is not None:
            if fresh.is_vacant != prop.is_vacant:
                logger.info("Vacancy changed since the snapshot for %s: %s → %s",
                            prop.building_name or prop.name,
                            prop.is_vacant, fresh.is_vacant)
            prop = fresh

        return prop, prop.is_vacant

    def _confirmed_vacant(self, alts: list[Property]) -> list[Property]:
        """Drop any suggestion that is no longer vacant, checked against WordPress.

        These are offered as rooms the customer could take instead, so proposing
        one let since the snapshot is the same error as misreporting the room
        they asked about — only more embarrassing, because we chose it. At most
        three requests, and only when the enquired room is unavailable.
        """
        confirmed: list[Property] = []
        for alt in alts:
            fresh = self._wp.refetch_one(alt.wp_id)
            if fresh is None:
                confirmed.append(alt)      # unreadable: keep the snapshot's view
            elif fresh.is_vacant:
                confirmed.append(fresh)
            else:
                logger.info("Dropping alternative %s — no longer vacant",
                            fresh.building_name or fresh.name)
        return confirmed

    def _build_email(self, inquiry: Inquiry,
                     reflection: Reflection | None = None
                     ) -> tuple[str, str, str, list[str]]:
        """
        Generate AI segments and assemble the full first mail.
        Returns (subject, plain_body, html_body). The plain body is stored as the
        operator-reviewable draft and is what the NG check scans.
        """
        invitation = (self._generator.generate_visit_invitation(inquiry.raw_body)
                      if _INCLUDE_VISIT_INVITATION else "")
        alt_intros: list[tuple[Property, str]] = []

        if inquiry.is_vacant and inquiry.matched_property:
            intro = self._generator.generate_property_intro(inquiry.matched_property)
        else:
            intro = ""
            base = inquiry.matched_property or self._reference_property(reflection)
            for alt in self._confirmed_vacant(self._scorer.find_alternatives(base, top_n=3)):
                alt_text = self._generator.generate_alt_intro(
                    inquiry.matched_property or alt, alt)
                alt_intros.append((alt, alt_text))

        assembler = EmailAssembler(self._company, is_business_hours())
        subject, body_plain = assembler.build_first_mail_parts(
            inquiry, intro, invitation, alt_intros)
        ai_segments = [s for s in [intro, invitation,
                                   *(t for _, t in alt_intros)] if s and s.strip()]
        return subject, body_plain, assembler.wrap_html(body_plain), ai_segments
