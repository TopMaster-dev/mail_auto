"""
法令表示 and the opt-out list.

特定電子メール法第4条第2号 — the mails carry campaign and membership links, so they
are 広告メール and must say how to refuse further ones.

宅建業法第34条 — they name specific properties, so the 取引態様 must be stated. The
statute asks for the 売主・代理・媒介 distinction; 一般/専任 is the kind of 媒介契約 on
top. Where the listing records nothing usable the line is omitted: saying nothing
is lawful, saying the wrong thing is not.

The opt-out must outlive the inquiry it arrived on. Stopping that follow-up alone
would let the same person start a fresh sequence by enquiring again — after
asking us to stop (client, 2026-10-13).
"""
import unittest
from datetime import datetime
from unittest.mock import MagicMock

from src.core.inquiry_processor import InquiryProcessor, _is_opt_out
from src.core.models import Inquiry, Property
from src.email_builder import followup_blocks as fb
from src.email_builder.assembler import EmailAssembler
from src.integrations.wp_client import deal_type

CO = {"staff_name": "新家", "tel_reservation": "0566-70-8282",
      "discount_url": "https://x/c", "mypage_url": "https://x/m", "line_url": "https://x/l"}


def _prop(deal=""):
    return Property(
        wp_id=1, name="x", url="https://x/p/", rent=64000, management_fee=0,
        layout="1R", nearest_station="", train_line="名鉄本線", city="知立市",
        walk_minutes=20, category=[], equipment=[], is_vacant=True,
        is_commission_free=False, area_sqm=50.0, building_type="",
        building_name="b", room_number="107", address="",
        access='名鉄本線「牛田」徒歩20分', deal_type=deal)


def _inq(prop):
    return Inquiry(id="i", received_at=datetime(2026, 10, 13), customer_name="大寺 啓未",
                   customer_email="a@b.c", inquiry_property_name="物件A",
                   inquiry_property_url="", raw_body="", is_vacant=True,
                   matched_property=prop)


class TestDealTypeExtraction(unittest.TestCase):
    """取引態様 lives inside the free-text 備考, as one ／-separated item."""

    def test_the_real_formats_parse(self):
        self.assertEqual(deal_type("普通借家契約／契約期間2年／取引態様：一般"), "媒介（一般）")
        self.assertEqual(deal_type("取引態様：専任"), "媒介（専任）")

    def test_it_parses_when_not_at_the_end_of_the_line(self):
        self.assertEqual(deal_type("取引態様：一般／その他条件あり"), "媒介（一般）")

    def test_a_generic_wording_states_only_what_is_certain(self):
        """「仲介」 identifies the relationship but not its exclusivity."""
        self.assertEqual(deal_type("取引態様：仲介"), "媒介")

    def test_a_combination_falls_back_rather_than_guessing(self):
        self.assertEqual(deal_type("取引態様：一般・専任"), "媒介")

    def test_nothing_usable_yields_nothing(self):
        for note in ("取引態様：", "備考のみ", "", None):
            self.assertEqual(deal_type(note), "")

    def test_full_width_colon_and_spacing(self):
        self.assertEqual(deal_type("取引態様 ： 一般"), "媒介（一般）")


class TestDealTypeInTheMail(unittest.TestCase):
    def test_it_is_shown_when_known(self):
        _, body = EmailAssembler(CO, True).build_first_mail_parts(
            _inq(_prop("媒介（一般）")), "紹介文", "", [])
        self.assertIn("取引態様：媒介（一般）", body)

    def test_the_line_is_omitted_when_unknown(self):
        _, body = EmailAssembler(CO, True).build_first_mail_parts(
            _inq(_prop("")), "紹介文", "", [])
        self.assertNotIn("取引態様", body)

    def test_follow_ups_state_it_too(self):
        _, body = EmailAssembler(CO, True).build_second_mail_parts(
            _inq(_prop("媒介（専任）")), "紹介文", "", [])
        self.assertIn("取引態様：媒介（専任）", body)

    def test_an_unknown_deal_type_leaves_no_blank_line_artefact(self):
        _, body = EmailAssembler(CO, True).build_first_mail_parts(
            _inq(_prop("")), "紹介文", "", [])
        self.assertNotIn("\n\n\n", body)


class TestOptOutNotice(unittest.TestCase):
    def test_every_mail_says_how_to_stop(self):
        a = EmailAssembler(CO, True)
        for build in (a.build_first_mail_parts, a.build_second_mail_parts,
                      a.build_third_mail_parts):
            _, body = build(_inq(_prop()), "紹介文", "", [])
            self.assertIn("配信停止", body)

    def test_it_sits_before_the_signature(self):
        _, body = EmailAssembler(CO, True).build_first_mail_parts(
            _inq(_prop()), "紹介文", "", [])
        self.assertLess(body.index(fb.OPT_OUT[:12]), body.index("■□━"))


class TestOptOutDetection(unittest.TestCase):
    def test_explicit_requests_are_recognised(self):
        for t in ("配信停止でお願いします", "配信不要です", "今後の連絡は不要です",
                  "unsubscribe", "送信停止を希望します"):
            self.assertTrue(_is_opt_out(t), t)

    def test_ordinary_replies_are_not_mistaken_for_opt_outs(self):
        """「不要」 alone appears in genuine questions."""
        for t in ("保証人は不要ですか", "敷金は不要とのことですが",
                  "内見を希望します", "ありがとうございます"):
            self.assertFalse(_is_opt_out(t), t)


class TestSuppressionIsEnforced(unittest.TestCase):
    def _proc(self, suppressed):
        p = InquiryProcessor.__new__(InquiryProcessor)
        p._sheets = MagicMock()
        p._suppressed = set(suppressed)
        return p

    def test_a_suppressed_address_is_recognised_case_insensitively(self):
        p = self._proc({"a@b.c"})
        self.assertTrue(p._is_suppressed("A@B.C"))
        self.assertTrue(p._is_suppressed(" a@b.c "))

    def test_an_unlisted_address_is_not_suppressed(self):
        self.assertFalse(self._proc({"a@b.c"})._is_suppressed("other@x.y"))

    def test_an_empty_address_is_not_suppressed(self):
        self.assertFalse(self._proc({"a@b.c"})._is_suppressed(""))


class TestSuppressionList(unittest.TestCase):
    def _client(self, existing=()):
        from src.integrations.sheets_client import SheetsClient
        sc = SheetsClient.__new__(SheetsClient)
        sc._ws = MagicMock()
        sc._retry = lambda fn: fn()
        sc._ws.return_value.col_values.return_value = ["メールアドレス", *existing]
        return sc

    def test_addresses_are_read_lower_cased(self):
        self.assertEqual(self._client(["A@B.C"]).read_suppressed(), {"a@b.c"})

    def test_a_new_address_is_appended(self):
        sc = self._client()
        sc.add_suppressed("x@y.z", "お客様からの配信停止希望")
        sc._ws.return_value.append_row.assert_called_once()

    def test_an_already_listed_address_is_not_duplicated(self):
        sc = self._client(["x@y.z"])
        sc.add_suppressed("X@Y.Z", "重複")
        sc._ws.return_value.append_row.assert_not_called()

    def test_a_blank_address_is_ignored(self):
        sc = self._client()
        sc.add_suppressed("", "r")
        sc._ws.return_value.append_row.assert_not_called()


if __name__ == "__main__":
    unittest.main()
