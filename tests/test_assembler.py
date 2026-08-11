"""
Body selection: what the customer is told must match what we actually know
about the property. Getting this wrong sends a false statement to a customer.
"""
import unittest
from datetime import datetime

from src.core.models import Inquiry, Property
from src.email_builder.assembler import EmailAssembler

# The "someone else applied first" claim — only ever true for a property we
# actually identified and found to be unavailable.
_TAKEN_PHRASE = "タッチ差"


def _prop(vacant: bool = True, commission_free: bool = False) -> Property:
    return Property(
        wp_id=1, name="テスト物件", url="https://rentmagazine.jp/estate/1",
        rent=80000, management_fee=5000, layout="1LDK", nearest_station="知立駅",
        train_line="名鉄三河線", city="知立市", walk_minutes=5,
        category=["デザイナーズ"], equipment=["追い焚き"], is_vacant=vacant,
        is_commission_free=commission_free, area_sqm=42.0, building_type="マンション",
    )


def _inquiry(prop, is_vacant) -> Inquiry:
    return Inquiry(
        id="i-1", received_at=datetime(2026, 6, 30, 10, 0),
        customer_name="山田 太郎", customer_email="taro@example.com",
        inquiry_property_name="テスト物件", inquiry_property_url="",
        raw_body="内見希望です。", is_vacant=is_vacant, matched_property=prop,
    )


class TestBodySelection(unittest.TestCase):
    def setUp(self):
        self.asm = EmailAssembler({"staff_name": "担当者"}, is_business_hours=True)
        self.alts = [(_prop(), "こちらもおすすめです。")]

    def test_vacant_property_shows_the_property_block(self):
        _subject, body = self.asm.build_first_mail_parts(
            _inquiry(_prop(vacant=True), True), "紹介文です。", "一言", self.alts)
        self.assertIn("◆お問い合わせ物件", body)
        self.assertIn("紹介文です。", body)
        self.assertNotIn(_TAKEN_PHRASE, body)

    def test_taken_property_says_so_and_offers_alternatives(self):
        _subject, body = self.asm.build_first_mail_parts(
            _inquiry(_prop(vacant=False), False), "", "一言", self.alts)
        self.assertIn(_TAKEN_PHRASE, body)
        self.assertIn("こちらもおすすめです。", body)

    def test_unidentified_property_never_claims_it_was_taken(self):
        # Property lookup failed: vacancy is unknown, so neither claim is safe.
        _subject, body = self.asm.build_first_mail_parts(
            _inquiry(None, None), "", "一言", self.alts)
        self.assertNotIn(_TAKEN_PHRASE, body)

    def test_unidentified_property_still_offers_alternatives(self):
        # This used to produce a mail with no property content at all, silently
        # discarding the alternatives that had just been generated.
        _subject, body = self.asm.build_first_mail_parts(
            _inquiry(None, None), "", "一言", self.alts)
        self.assertIn("◆おすすめ物件", body)
        self.assertIn("こちらもおすすめです。", body)

    def test_missing_staff_name_does_not_raise(self):
        asm = EmailAssembler({}, is_business_hours=True)
        subject, _html = asm.build_second_mail(_inquiry(_prop(), True), "i", "v", [])
        self.assertIn("テスト物件", subject)

    def test_out_of_hours_note_only_outside_business_hours(self):
        inq = _inquiry(_prop(), True)
        _s, in_hours = EmailAssembler(
            {"staff_name": "担当"}, True).build_first_mail_parts(inq, "x", "y", [])
        _s, out_of_hours = EmailAssembler(
            {"staff_name": "担当"}, False).build_first_mail_parts(inq, "x", "y", [])
        self.assertNotIn("営業時間外", in_hours)
        self.assertIn("営業時間外", out_of_hours)


class TestFollowupParts(unittest.TestCase):
    """The _parts variants exist so the NG scan can read the plain body."""

    def setUp(self):
        self.asm = EmailAssembler({"staff_name": "担当者"}, is_business_hours=True)
        self.inq = _inquiry(_prop(), True)

    def test_second_mail_parts_matches_html_build(self):
        subject, plain = self.asm.build_second_mail_parts(self.inq, "紹介文", "一言", [])
        subject_html, html = self.asm.build_second_mail(self.inq, "紹介文", "一言", [])
        self.assertEqual(subject, subject_html)
        self.assertIn("紹介文", plain)
        self.assertEqual(html, EmailAssembler.wrap_html(plain))

    def test_third_mail_parts_matches_html_build(self):
        subject, plain = self.asm.build_third_mail_parts(self.inq, "紹介文", "一言", [])
        subject_html, html = self.asm.build_third_mail(self.inq, "紹介文", "一言", [])
        self.assertEqual(subject, subject_html)
        self.assertEqual(html, EmailAssembler.wrap_html(plain))

    def test_html_escapes_the_plain_body(self):
        self.assertIn("&lt;script&gt;", EmailAssembler.wrap_html("<script>"))


if __name__ == "__main__":
    unittest.main()
