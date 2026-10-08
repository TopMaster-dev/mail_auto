"""
An edited template actually becomes the mail that is sent.

{物件情報} carries the one part the operator cannot write by hand: an available
room renders 「◆お問い合わせ物件」, an unavailable one renders the 「◆おすすめ物件」
list. Everything around it is theirs.
"""
import unittest
from datetime import datetime
from unittest.mock import MagicMock

from src.core.models import Inquiry, Property
from src.email_builder import template_store as ts
from src.email_builder.assembler import EmailAssembler

CO = {"staff_name": "新家", "tel_reservation": "0566-70-8282",
      "discount_url": "https://x/c", "mypage_url": "https://x/m", "line_url": "https://x/l"}

BODY = """\
{顧客名}様

レントマガジン株式会社の{担当者名}です。

{物件情報}

最寄駅は{最寄駅}です。
{署名}"""


def _prop(vacant=True, name="ボニートロッサ"):
    return Property(
        wp_id=1, name=name, url="https://rentmagazine.jp/estate/x/", rent=64000,
        management_fee=0, layout="1R", nearest_station="", train_line="名鉄本線",
        city="知立市", walk_minutes=20, category=[], equipment=[], is_vacant=vacant,
        is_commission_free=False, area_sqm=50.0, building_type="",
        building_name=name, room_number="107", address="",
        access='名鉄本線「牛田」徒歩20分')


def _inquiry(vacant=True, prop=None):
    return Inquiry(
        id="i", received_at=datetime(2026, 10, 7), customer_name="大寺 啓未",
        customer_email="a@b.c", inquiry_property_name="ボニートロッサI107",
        inquiry_property_url="", raw_body="", is_vacant=vacant,
        matched_property=prop if prop is not None else _prop(vacant))


def _store(subject, body, kind=ts.FIRST):
    sheets = MagicMock()
    sheets.read_templates.return_value = [
        {"テンプレート名": ts.SUBJECT_ROW[kind], "内容": subject},
        {"テンプレート名": ts.BODY_ROW[kind], "内容": body},
    ]
    s = ts.TemplateStore(sheets)
    s.refresh()
    return s


class TestFirstMailFromTemplate(unittest.TestCase):
    def setUp(self):
        self.a = EmailAssembler(CO, True, templates=_store("【{物件名}】ご連絡", BODY))

    def test_the_subject_comes_from_the_template(self):
        subject, _ = self.a.build_first_mail_parts(_inquiry(), "紹介文", "", [])
        self.assertEqual(subject, "【ボニートロッサI107】ご連絡")

    def test_the_body_is_the_operators_wording(self):
        _, body = self.a.build_first_mail_parts(_inquiry(), "紹介文", "", [])
        self.assertTrue(body.startswith("大寺 啓未様"))
        self.assertIn("レントマガジン株式会社の新家です。", body)
        self.assertIn("最寄駅は名鉄本線「牛田」です。", body)

    def test_no_placeholder_survives_into_the_mail(self):
        _, body = self.a.build_first_mail_parts(_inquiry(), "紹介文", "", [])
        self.assertNotIn("{", body)

    def test_an_available_room_renders_the_enquired_property(self):
        _, body = self.a.build_first_mail_parts(_inquiry(), "紹介文です", "", [])
        self.assertIn("◆お問い合わせ物件", body)
        self.assertIn("物件名：ボニートロッサI107", body)
        self.assertIn("紹介文です", body)
        self.assertNotIn("◆おすすめ物件", body)

    def test_an_unavailable_room_renders_recommendations_instead(self):
        alt = _prop(name="オリーブ")
        _, body = self.a.build_first_mail_parts(
            _inquiry(vacant=False), "", "", [(alt, "代替の紹介文")])
        self.assertIn("◆おすすめ物件", body)
        self.assertIn("代替の紹介文", body)
        self.assertNotIn("◆お問い合わせ物件", body)


class TestFollowUpsFromTemplate(unittest.TestCase):
    def test_the_second_mail_uses_its_own_template(self):
        a = EmailAssembler(CO, True,
                           templates=_store("2nd件名", "2nd本文 {顧客名}", ts.SECOND))
        subject, body = a.build_second_mail_parts(_inquiry(), "紹介文", "", [])
        self.assertEqual(subject, "2nd件名")
        self.assertIn("2nd本文 大寺 啓未", body)

    def test_an_unconfigured_third_still_uses_the_built_in(self):
        a = EmailAssembler(CO, True,
                           templates=_store("2nd件名", "2nd本文", ts.SECOND))
        _, body = a.build_third_mail_parts(_inquiry(), "紹介文", "", [])
        self.assertIn("まずは比較してみたい", body)      # built-in 3rd lead


class TestBuiltInRemainsTheDefault(unittest.TestCase):
    def test_no_store_means_the_built_in_text(self):
        a = EmailAssembler(CO, True)
        subject, body = a.build_first_mail_parts(_inquiry(), "紹介文", "", [])
        self.assertIn("お問い合わせありがとうございます", subject)
        self.assertIn("この度はお問い合わせ頂きありがとうございます", body)

    def test_an_empty_sheet_means_the_built_in_text(self):
        sheets = MagicMock()
        sheets.read_templates.return_value = []
        store = ts.TemplateStore(sheets)
        store.refresh()
        a = EmailAssembler(CO, True, templates=store)
        _, body = a.build_first_mail_parts(_inquiry(), "紹介文", "", [])
        self.assertIn("この度はお問い合わせ頂きありがとうございます", body)

    def test_a_broken_template_is_not_rendered(self):
        """It must not silently fall back either — the caller refuses to send."""
        store = _store("【{物件名称}】", BODY)
        self.assertTrue(store.blocking_errors(ts.FIRST))
        a = EmailAssembler(CO, True, templates=store)
        _, body = a.build_first_mail_parts(_inquiry(), "紹介文", "", [])
        self.assertIn("この度はお問い合わせ頂きありがとうございます", body)


if __name__ == "__main__":
    unittest.main()
