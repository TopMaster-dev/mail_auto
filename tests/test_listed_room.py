"""
Which room is on the market comes from 部屋番号, not from the 物件名.

Client (2026-09-10): 「WordPress上の『物件名』については、当初撮影してきたお部屋の号室を
物件名に含めて登録しているケースが多く」「『部屋番号』の項目には、基本的に現在募集している
実際の号室が登録されています」 — so パストラーレ富士松_307 is currently let as room 107,
and SUUMO's room must be matched against 部屋番号.
"""
import unittest

from src.core.models import Property
from src.integrations.wp_client import WordPressClient


def _prop(wp_id, building_name, room_number="", vacant=True, rent=60000):
    return Property(
        wp_id=wp_id, name=f"title-{wp_id}", url=f"https://x/{wp_id}", rent=rent,
        management_fee=0, layout="1R", nearest_station="", train_line="",
        city="", walk_minutes=0, category=[], equipment=[], is_vacant=vacant,
        is_commission_free=False, area_sqm=30.0, building_type="",
        building_name=building_name, room_number=room_number, address="", access="")


def _client(props):
    wp = WordPressClient.__new__(WordPressClient)
    wp._properties = props
    return wp


class TestRoomNumberFieldWins(unittest.TestCase):
    """The real パストラーレ富士松 record: name says 307, 部屋番号 says 107."""

    def setUp(self):
        # 部屋番号 arrives as 「107（1階部分）」 and is parsed to "107" at load time.
        self.wp = _client([_prop(1, "パストラーレ富士松_307", room_number="107")])

    def test_the_listed_room_matches(self):
        prop, display = self.wp.resolve_by_formal_name("パストラーレ富士松107")
        self.assertIsNotNone(prop)
        self.assertEqual(prop.wp_id, 1)
        self.assertEqual(display, "パストラーレ富士松107")

    def test_the_photographed_room_in_the_name_does_not(self):
        """307 is the room they photographed, not the room being let."""
        prop, display = self.wp.resolve_by_formal_name("パストラーレ富士松307")
        self.assertIsNotNone(prop)          # same building
        self.assertEqual(display, "パストラーレ富士松")   # but no room claimed

    def test_room_number_is_parsed_out_of_its_suffix(self):
        """部屋番号 is registered as 「107（1階部分）」 and must reduce to 107."""
        from src.integrations.wp_client import _room_number
        self.assertEqual(_room_number("107（1階部分）"), "107")
        self.assertEqual(_room_number("205（2階部分）"), "205")


class TestPickingBetweenRooms(unittest.TestCase):
    def test_the_right_room_is_chosen_by_its_number(self):
        wp = _client([
            _prop(1, "オリーブ_201", room_number="305"),
            _prop(2, "オリーブ_102", room_number="201"),
        ])
        prop, display = wp.resolve_by_formal_name("オリーブ201")
        self.assertEqual(prop.wp_id, 2)           # 部屋番号 201, not the name _201
        self.assertEqual(display, "オリーブ201")

    def test_zero_padding_is_ignored(self):
        wp = _client([_prop(1, "サンハイツ", room_number="0308")])
        prop, _ = wp.resolve_by_formal_name("サンハイツ308")
        self.assertEqual(prop.wp_id, 1)


class TestFallbackWhenTheFieldIsBlank(unittest.TestCase):
    """Some listings carry no 部屋番号; then the name is the only signal."""

    def test_name_room_is_used_when_no_room_number_is_registered(self):
        wp = _client([_prop(1, "オリーブ_201"), _prop(2, "オリーブ_102")])
        prop, display = wp.resolve_by_formal_name("オリーブ201")
        self.assertEqual(prop.wp_id, 1)
        self.assertEqual(display, "オリーブ201")


class TestBuildingOnlyNames(unittest.TestCase):
    """物件名 sometimes carries no room at all."""

    def test_building_only_registration_still_matches(self):
        wp = _client([_prop(1, "EIGHT BASEC棟", room_number="01")])
        prop, display = wp.resolve_by_formal_name("EIGHT BASEC棟2")
        self.assertEqual(prop.wp_id, 1)
        self.assertEqual(display, "EIGHT BASEC棟")   # room 2 unconfirmed → dropped

    def test_building_only_on_both_sides(self):
        wp = _client([_prop(1, "EIGHT BASEC棟", room_number="01")])
        prop, display = wp.resolve_by_formal_name("EIGHT BASEC棟")
        self.assertEqual(prop.wp_id, 1)
        self.assertEqual(display, "EIGHT BASEC棟")


class TestUnrelatedBuildingsStayApart(unittest.TestCase):
    def test_a_different_building_is_not_matched(self):
        wp = _client([_prop(1, "パストラーレ富士松_307", room_number="107")])
        prop, _ = wp.resolve_by_formal_name("bonitorosa107")
        self.assertIsNone(prop)

    def test_vacant_listing_is_preferred_within_a_building(self):
        wp = _client([
            _prop(1, "みどり荘_101", room_number="", vacant=False),
            _prop(2, "みどり荘_102", room_number="", vacant=True),
        ])
        prop, _ = wp.resolve_by_formal_name("みどり荘")
        self.assertEqual(prop.wp_id, 2)


if __name__ == "__main__":
    unittest.main()
