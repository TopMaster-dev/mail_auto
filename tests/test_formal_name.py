"""
Resolving a portal's 物件名 to a listing (client item 2, 2026-08-21).

SUUMO identifies a property by its formal name — サンステージエクセル203 — while
the WordPress post title is marketing copy (「都市の利便性」). The formal name
lives in the buildname taxonomy, written 建物名_部屋番号.

Client's rule when only the building matches: introduce the building and do not
refer to the room number.
"""
import unittest

from src.core.models import Property
from src.integrations.wp_client import WordPressClient, formal_key


def _prop(wp_id, building_name, vacant=True, rent=70000, title="サイト掲載タイトル"):
    return Property(
        wp_id=wp_id, name=title, url=f"https://rentmagazine.jp/estate/{wp_id}",
        rent=rent, management_fee=5000, layout="1LDK", nearest_station="",
        train_line="名鉄線", city="安城市", walk_minutes=8, category=[],
        equipment=[], is_vacant=vacant, is_commission_free=False,
        area_sqm=40.0, building_type="マンション", building_name=building_name)


def _client(props):
    wp = WordPressClient("https://x", "", "", 100, {}, {})
    wp._properties = props
    return wp


class TestFormalKey(unittest.TestCase):
    def test_separator_and_width_folding(self):
        # SUUMO: オリーブ201   WordPress: オリーブ_201
        self.assertEqual(formal_key("オリーブ201"), formal_key("オリーブ_201"))

    def test_roman_numeral_folding(self):
        # SUUMO writes III, WordPress writes Ⅲ — NFKC folds them.
        self.assertEqual(formal_key("Jack hachimanIII301"),
                         formal_key("Jack hachimanⅢ_301"))

    def test_blank(self):
        self.assertEqual(formal_key(""), "")


class TestRoomLevelMatch(unittest.TestCase):
    def test_exact_room_keeps_the_full_name(self):
        wp = _client([_prop(1, "オリーブ_201"), _prop(2, "オリーブ_102")])
        prop, display = wp.resolve_by_formal_name("オリーブ201")
        self.assertEqual(prop.wp_id, 1)
        self.assertEqual(display, "オリーブ201")

    def test_roman_numeral_room_resolves(self):
        wp = _client([_prop(9, "Jack hachimanⅢ_301")])
        prop, display = wp.resolve_by_formal_name("Jack hachimanIII301")
        self.assertEqual(prop.wp_id, 9)
        self.assertEqual(display, "Jack hachimanIII301")


class TestBuildingLevelMatch(unittest.TestCase):
    """案B — introduce the building, drop the room number."""

    def test_unlisted_room_falls_back_to_the_building(self):
        # Customer asked about 203; only 303 is listed.
        wp = _client([_prop(1, "サンステージエクセル_303")])
        prop, display = wp.resolve_by_formal_name("サンステージエクセル203")
        self.assertEqual(prop.wp_id, 1)
        self.assertEqual(display, "サンステージエクセル")
        self.assertNotIn("203", display)
        self.assertNotIn("303", display)

    def test_prefers_a_vacant_room_in_the_building(self):
        wp = _client([_prop(1, "Studio・G_601", vacant=False),
                      _prop(2, "Studio・G_602", vacant=True)])
        prop, _ = wp.resolve_by_formal_name("Studio・G605")
        self.assertEqual(prop.wp_id, 2)

    def test_does_not_match_a_longer_building_name(self):
        # オリーブ must not resolve to オリーブハイツ.
        wp = _client([_prop(1, "オリーブハイツ_301")])
        prop, display = wp.resolve_by_formal_name("オリーブ201")
        self.assertIsNone(prop)
        self.assertEqual(display, "")

    def test_no_match_returns_nothing(self):
        wp = _client([_prop(1, "まったく別の建物_101")])
        self.assertEqual(wp.resolve_by_formal_name("オリーブ201"), (None, ""))


class TestDisplayedNameInMail(unittest.TestCase):
    def test_property_block_shows_the_enquiry_name_not_the_title(self):
        from datetime import datetime
        from src.core.models import Inquiry
        from src.email_builder.assembler import EmailAssembler

        prop = _prop(1, "オリーブ_201", title="都市の利便性")
        inquiry = Inquiry(
            id="i", received_at=datetime(2026, 8, 21, 10, 0),
            customer_name="三宅しのぶ", customer_email="a@b.c",
            inquiry_property_name="オリーブ201", inquiry_property_url="",
            raw_body="", is_vacant=True, matched_property=prop)
        _, body = EmailAssembler({"staff_name": "担当"}, True).build_first_mail_parts(
            inquiry, "紹介文", "", [])
        self.assertIn("物件名：オリーブ201", body)
        self.assertNotIn("物件名：都市の利便性", body)

    def test_recommendations_keep_their_titles(self):
        from datetime import datetime
        from src.core.models import Inquiry
        from src.email_builder.assembler import EmailAssembler

        alt = _prop(2, "べつの建物_101", title="都市生活を楽しみたい方に")
        inquiry = Inquiry(
            id="i", received_at=datetime(2026, 8, 21, 10, 0),
            customer_name="三宅しのぶ", customer_email="a@b.c",
            inquiry_property_name="オリーブ201", inquiry_property_url="",
            raw_body="", is_vacant=False,
            matched_property=_prop(1, "オリーブ_201", vacant=False))
        _, body = EmailAssembler({"staff_name": "担当"}, True).build_first_mail_parts(
            inquiry, "", "", [(alt, "代替紹介文")])
        self.assertIn("都市生活を楽しみたい方に", body)


if __name__ == "__main__":
    unittest.main()


class TestBuildingRegisteredWithoutRoomNumber(unittest.TestCase):
    """Some buildings are registered as just the building: EIGHT BASEC棟."""

    def test_building_without_room_number_still_matches(self):
        wp = _client([_prop(1, "EIGHT BASEC棟")])
        prop, display = wp.resolve_by_formal_name("EIGHT BASEC棟2")
        self.assertEqual(prop.wp_id, 1)
        self.assertEqual(display, "EIGHT BASEC棟")

    def test_dotted_building_without_room_number(self):
        wp = _client([_prop(1, "Studio・G")])
        prop, display = wp.resolve_by_formal_name("Studio・G601")
        self.assertEqual(prop.wp_id, 1)
        self.assertEqual(display, "Studio・G")

    def test_still_rejects_a_longer_building_name(self):
        wp = _client([_prop(1, "オリーブハイツ")])
        self.assertEqual(wp.resolve_by_formal_name("オリーブ201"), (None, ""))
