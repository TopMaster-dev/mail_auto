"""
2nd / 3rd follow-up mails against the client's 「2nd・3rdメールテンプレート整理版」.

Every block in that template is asserted here, because the previous
implementation silently reused the 1st-mail body and none of it was present.
"""
import unittest
from datetime import date, datetime

from src.core.models import Inquiry, Property
from src.email_builder import followup_blocks as fb
from src.email_builder.assembler import EmailAssembler

_CO = {
    "staff_name": "新家",
    "tel_reservation": "0566-70-8282",
    "discount_url": "https://rentmagazine.jp/campaign1/",
    "mypage_url": "https://rentmagazine.jp/mypage/",
    "line_url": "https://liff.line.me/1660986243-XXXX",
}


def _prop(commission_free=False, access='名鉄本線「牛田」徒歩20分\r\n東海道線「東刈谷」徒歩26分'):
    return Property(
        wp_id=1, name="海外テイストなビッグワンルーム",
        url="https://rentmagazine.jp/estate/1", rent=65000, management_fee=3210,
        layout="1R", nearest_station="", train_line="名鉄本線", city="知立市",
        walk_minutes=20, category=[], equipment=[], is_vacant=True,
        is_commission_free=commission_free, area_sqm=50.0,
        building_type="マンション", building_name="bonitorosa Ⅰ・Ⅱ",
        room_number="205", address="", access=access)


def _inquiry(prop=None, vacant=True):
    return Inquiry(
        id="i", received_at=datetime(2026, 8, 31, 10, 0), customer_name="大寺 啓未",
        customer_email="a@b.c", inquiry_property_name="ボニートロッサⅠ205",
        inquiry_property_url="", raw_body="", is_vacant=vacant,
        matched_property=prop if prop is not None else _prop())


def _second(**kw):
    return EmailAssembler(_CO, True).build_second_mail_parts(
        _inquiry(**kw), "AI紹介文です", "", [])


def _third(**kw):
    return EmailAssembler(_CO, True).build_third_mail_parts(
        _inquiry(**kw), "AI紹介文です", "", [])


class TestSubjectAndOpening(unittest.TestCase):
    def test_subject(self):
        subject, _ = _second()
        self.assertEqual(subject, "【ボニートロッサⅠ205】先日はお問い合わせありがとうございます！")

    def test_addressed_to_the_customer(self):
        _, body = _second()
        self.assertTrue(body.startswith("大寺 啓未様"))

    def test_staff_name_from_config(self):
        _, body = _second()
        self.assertIn("レントマガジン株式会社の新家と申します", body)


class TestSecondVsThird(unittest.TestCase):
    def test_second_reconfirms_a_viewing(self):
        _, body = _second()
        self.assertIn("その後いかがでしょうか", body)
        self.assertNotIn("まずは比較してみたい", body)

    def test_third_shifts_to_comparison(self):
        _, body = _third()
        self.assertIn("まずは比較してみたい", body)
        self.assertNotIn("その後いかがでしょうか", body)


class TestTemplateBlocks(unittest.TestCase):
    """Each block the client listed must actually appear."""

    def setUp(self):
        _, self.body = _second()

    def test_property_block_with_initial_cost_note(self):
        self.assertIn("◆お問い合わせ物件", self.body)
        self.assertIn("物件名：ボニートロッサⅠ205", self.body)
        self.assertIn("※上記URL内に初期費用を記載させていただきました", self.body)

    def test_ai_intro_present(self):
        self.assertIn("AI紹介文です", self.body)

    def test_three_concrete_date_proposals(self):
        for label in ("第一希望", "第二希望", "第三希望"):
            self.assertIn(label, self.body)
        self.assertIn("下記の日程はご都合いかがでしょうか", self.body)

    def test_fallback_schedule_template(self):
        self.assertIn("上記日程が合わない場合", self.body)
        self.assertIn("大寺 啓未様のご都合のよいご希望日", self.body)

    def test_station_pickup(self):
        self.assertIn("最寄駅までお迎えいたします", self.body)
        self.assertIn("【最寄駅】名鉄本線「牛田」", self.body)

    def test_phone_booking(self):
        self.assertIn("0566-70-8282", self.body)
        self.assertIn("営業時間：10時〜18時", self.body)

    def test_web_member_and_line(self):
        self.assertIn("https://rentmagazine.jp/mypage/", self.body)
        self.assertIn("https://liff.line.me/1660986243-XXXX", self.body)

    def test_condition_hearing_form(self):
        self.assertIn("【ご希望条件】", self.body)
        self.assertIn("家　賃", self.body)
        self.assertIn("その他こだわり条件", self.body)

    def test_closing_and_signature(self):
        self.assertIn("ご検討よろしくお願いいたします", self.body)
        self.assertIn("レントマガジン株式会社 / Rent Magazine", self.body)


class TestCommissionBranch(unittest.TestCase):
    def test_commission_free_property(self):
        _, body = _second(prop=_prop(commission_free=True))
        self.assertIn("仲介手数料無料でご案内させていただいております", body)
        self.assertNotIn("女性割引", body)

    def test_paid_property_gets_the_discount_link(self):
        _, body = _second(prop=_prop(commission_free=False))
        self.assertIn("女性割引", body)
        self.assertIn("https://rentmagazine.jp/campaign1/", body)


class TestVacancyBranch(unittest.TestCase):
    def test_unavailable_room_offers_alternatives(self):
        alt = _prop()
        alt.name = "別のおすすめ物件"
        _, body = EmailAssembler(_CO, True).build_second_mail_parts(
            _inquiry(vacant=False), "", "", [(alt, "代替の紹介文")])
        self.assertIn("◆おすすめ物件", body)
        self.assertIn("代替の紹介文", body)
        self.assertNotIn("◆お問い合わせ物件", body)


class TestScheduleSlots(unittest.TestCase):
    def test_three_future_dates_with_weekday(self):
        lines = fb.proposed_slots(date(2026, 8, 31))   # Monday
        self.assertEqual(len(lines), 3)
        self.assertIn("9月1日(火)", lines[0])
        self.assertIn("9月2日(水)", lines[1])
        self.assertIn("9月3日(木)", lines[2])

    def test_slots_stay_within_business_hours(self):
        for line in fb.proposed_slots(date(2026, 8, 31)):
            self.assertTrue(any(t in line for t in ("10:00", "14:00", "16:00")), line)


class TestNoUnverifiedClaims(unittest.TestCase):
    """The client's rules forbid asserting demand or stock for a listing."""

    def test_no_popularity_or_stock_claims(self):
        _, body = _second()
        for phrase in ("大変人気", "残り1室", "早いもの順", "人気物件"):
            self.assertNotIn(phrase, body)


class TestAfterCallVariant(unittest.TestCase):
    """電話不在時パターン — only when an operator recorded the call."""

    def test_marker_detection(self):
        for memo in ("電話済", "8/31 架電、不在", "TEL済み", "電話不在のため"):
            self.assertTrue(fb.called_before_followup(memo), memo)

    def test_unrelated_memo_does_not_claim_a_call(self):
        for memo in ("", "電話番号が未記入", "メールのみ希望", None):
            self.assertFalse(fb.called_before_followup(memo), memo)

    def test_after_call_opening_replaces_the_normal_one(self):
        _, body = EmailAssembler(_CO, True).build_second_mail_parts(
            _inquiry(), "AI紹介文です", "", [], after_call=True)
        self.assertIn("先ほどお電話にて", body)
        self.assertIn("ボニートロッサⅠ205", body)
        self.assertNotIn("先日はお問い合わせ誠にありがとうございます", body)
        # The opening already names the property and says why we are writing;
        # the lead must not repeat it a line later.
        self.assertEqual(body.count("ボニートロッサⅠ205"), 2)   # opening + 物件欄
        self.assertNotIn("先日お問い合わせいただきました", body)
        self.assertIn("ぜひ一度ご内覧いただければと思います", body)

    def test_after_call_third_keeps_the_comparison_pitch(self):
        _, body = EmailAssembler(_CO, True).build_third_mail_parts(
            _inquiry(), "AI紹介文です", "", [], after_call=True)
        self.assertIn("先ほどお電話にて", body)
        self.assertIn("まずは比較してみたい", body)
        self.assertNotIn("改めてご連絡させていただきました", body)

    def test_default_is_the_normal_opening(self):
        _, body = _second()
        self.assertNotIn("先ほどお電話にて", body)


class TestSchedulerPassesTheMemo(unittest.TestCase):
    def test_memo_reaches_the_opening(self):
        """A memo of 架電 must produce the after-call opening end-to-end."""
        inq = _inquiry()
        inq.staff_memo = "8/31 架電、不在"
        _, body = EmailAssembler(_CO, True).build_second_mail_parts(
            inq, "AI紹介文です", "", [],
            after_call=fb.called_before_followup(inq.staff_memo))
        self.assertIn("先ほどお電話にて", body)


if __name__ == "__main__":
    unittest.main()
