"""
Changes requested in the client's 2026-08-19 review, each tied to its item.

Kept together so the reason for each rule stays visible: several of these look
like arbitrary wording choices unless you know they were asked for.
"""
import unittest
from datetime import datetime

from src.core.models import Inquiry, Property, fmt_dt
from src.email_builder.assembler import EmailAssembler

_COMPANY = {"staff_name": "担当者"}


def _prop(wp_id=1, name="おすすめ物件A", commission_free=False):
    return Property(
        wp_id=wp_id, name=name, url=f"https://rentmagazine.jp/estate/{wp_id}",
        rent=70000, management_fee=5000, layout="2LDK", nearest_station="知立",
        train_line="名鉄三河線", city="知立市", walk_minutes=8,
        category=["ペット相談"], equipment=["オートロック"], is_vacant=True,
        is_commission_free=commission_free, area_sqm=55.0, building_type="マンション")


def _inquiry(matched=None, vacant=None):
    return Inquiry(
        id="i-1", received_at=datetime(2026, 8, 16, 19, 8),
        customer_name="石原慎也", customer_email="ishihara@example.com",
        inquiry_property_name="ガレージ付き戸建", inquiry_property_url="",
        raw_body="内覧したいです", is_vacant=vacant, matched_property=matched)


class TestItem1ReceivedAt(unittest.TestCase):
    """受信日時 must be shown in JST, whatever offset the sender stamped."""

    def test_foreign_offset_converted_to_jst(self):
        from datetime import timedelta, timezone
        # The WordPress form stamps -0400; 03:08 there is 16:08 JST same day.
        stamped = datetime(2026, 8, 16, 3, 8, 29, tzinfo=timezone(timedelta(hours=-4)))
        self.assertEqual(fmt_dt(stamped), "2026-08-16 16:08:29")

    def test_naive_value_left_alone(self):
        self.assertEqual(fmt_dt(datetime(2026, 8, 16, 19, 8, 0)),
                         "2026-08-16 19:08:00")

    def test_none_is_blank(self):
        self.assertEqual(fmt_dt(None), "")


class TestItem2Addressee(unittest.TestCase):
    """The mail must be addressed to the customer, not the form sender."""

    def test_greeting_uses_customer_name(self):
        _, body = EmailAssembler(_COMPANY, True).build_first_mail_parts(
            _inquiry(matched=_prop(), vacant=True), "紹介文", "", [])
        self.assertIn("石原慎也様", body)
        self.assertNotIn("レントマガジン株式会社 / Rent Magazine様", body)


class TestItem3UnavailableWording(unittest.TestCase):
    """紹介NG文 must match the client's supplied template (spec §6-3)."""

    def test_exact_wording(self):
        _, body = EmailAssembler(_COMPANY, True).build_first_mail_parts(
            _inquiry(matched=_prop(), vacant=False), "", "", [(_prop(2), "紹介文")])
        self.assertIn(
            "ご紹介できない状況ですので弊社がオススメの物件をご紹介させていただきます。",
            body)
        self.assertNotIn("そこで弊社が", body)


class TestItem5InitialCostNote(unittest.TestCase):
    """初期費用の案内 appears once per mail, after all the listings."""

    def test_stated_once_for_three_alternatives(self):
        alts = [(_prop(2, "A"), "文A"), (_prop(3, "B"), "文B"), (_prop(4, "C"), "文C")]
        _, body = EmailAssembler(_COMPANY, True).build_first_mail_parts(
            _inquiry(matched=_prop(), vacant=False), "", "", alts)
        self.assertEqual(body.count("初期費用に記載させて"), 1)

    def test_note_follows_the_listings(self):
        alts = [(_prop(2, "A"), "文A"), (_prop(3, "B"), "文B")]
        _, body = EmailAssembler(_COMPANY, True).build_first_mail_parts(
            _inquiry(matched=_prop(), vacant=False), "", "", alts)
        self.assertGreater(body.index("初期費用に記載させて"), body.rindex("◆おすすめ物件"))

    def test_vacant_path_still_states_it_once(self):
        _, body = EmailAssembler(_COMPANY, True).build_first_mail_parts(
            _inquiry(matched=_prop(), vacant=True), "紹介文", "", [])
        self.assertEqual(body.count("初期費用に記載させて"), 1)


class TestItem5VisitInvitationWithdrawn(unittest.TestCase):
    """The AI 来店誘導前の一言 was withdrawn outright, not replaced."""

    def test_processor_flag_is_off(self):
        from src.core.inquiry_processor import _INCLUDE_VISIT_INVITATION
        self.assertFalse(_INCLUDE_VISIT_INVITATION)

    def test_empty_invitation_leaves_no_gap(self):
        _, body = EmailAssembler(_COMPANY, True).build_first_mail_parts(
            _inquiry(matched=_prop(), vacant=True), "紹介文", "", [])
        self.assertNotIn("\n\n\n", body)


if __name__ == "__main__":
    unittest.main()
