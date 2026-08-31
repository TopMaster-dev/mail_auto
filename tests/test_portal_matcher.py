"""
Attribute matching for portal reflections (client, 2026-08-22).

A portal and the site register the same building in different scripts —
SUUMO sends ボニートロッサⅠ where the site holds `bonitorosa Ⅰ・Ⅱ` — so names
cannot be reconciled by folding characters. These attributes do not depend on
spelling.

A wrong match would describe the wrong flat to a customer, so acceptance is
strict and anything short of it must return None.
"""
import unittest

from src.core.models import Property
from src.core.reflection import Reflection
from src.matching import portal_matcher as pm


def _prop(wp_id, *, building="bonitorosa Ⅰ・Ⅱ", room="205", rent=65000,
          space=50.0, layout="1R", vacant=True, city="知立市",
          access='名鉄本線「牛田」徒歩20分'):
    return Property(
        wp_id=wp_id, name="海外テイストなビッグワンルーム",
        url=f"https://rentmagazine.jp/estate/{wp_id}", rent=rent,
        management_fee=3210, layout=layout, nearest_station="", train_line="名鉄本線",
        city=city, walk_minutes=20, category=[], equipment=[], is_vacant=vacant,
        is_commission_free=False, area_sqm=space, building_type="マンション",
        building_name=building, room_number=room, address="", access=access)


def _ref(name="ボニートロッサⅠ205", *, layout="1R", area="50平米",
         rent="6.5万円", station="名鉄本線/牛田", addr="愛知県知立市八ツ田町"):
    return Reflection(
        source="suumo", customer_name="大寺 啓未", customer_email="a@b.c",
        property_name=name, property_url="", inquiry_text="見学したい",
        extras={"間取り": layout, "専有面積": area, "賃料": rent,
                "最寄り駅": station, "所在地": addr})


class TestParsers(unittest.TestCase):
    def test_rent_in_man_yen(self):
        self.assertEqual(pm.rent_yen("6.3万円"), 63000)
        self.assertEqual(pm.rent_yen("63,000円"), 63000)
        self.assertEqual(pm.rent_yen(""), 0)

    def test_area(self):
        self.assertEqual(pm.area_sqm("40.32平米"), 40.32)
        self.assertEqual(pm.area_sqm("50"), 50.0)

    def test_room_from_name(self):
        self.assertEqual(pm.room_from_name("ボニートロッサⅠ205"), "205")
        self.assertEqual(pm.room_from_name("ボニートロッサⅠ 205号室"), "205")
        self.assertEqual(pm.room_from_name("EIGHT BASEC棟"), "")

    def test_strip_room(self):
        self.assertEqual(pm.strip_room("ボニートロッサⅠ205"), "ボニートロッサⅠ")
        self.assertEqual(pm.strip_room("ボニートロッサⅠ 205号室"), "ボニートロッサⅠ")

    def test_station_names(self):
        self.assertEqual(pm.station_names("名鉄西尾線/南安城"), ["名鉄西尾線", "南安城"])


class TestMatchesAcrossScripts(unittest.TestCase):
    """The case the client reported: katakana enquiry, romaji registration."""

    def test_katakana_enquiry_finds_romaji_listing(self):
        m = pm.match(_ref(), [_prop(48329)])
        self.assertIsNotNone(m)
        self.assertEqual(m.prop.wp_id, 48329)
        self.assertTrue(m.room_level)
        self.assertIn("部屋番号", m.basis)

    def test_picks_the_right_room_in_the_building(self):
        rooms = [_prop(1, room="103", rent=61000), _prop(2, room="205", rent=65000),
                 _prop(3, room="206", rent=65000), _prop(4, room="307", rent=66000)]
        m = pm.match(_ref(), rooms)
        self.assertEqual(m.prop.room_number, "205")

    def test_display_keeps_the_room_when_confirmed(self):
        m = pm.match(_ref(), [_prop(1)])
        self.assertEqual(m.display_name, "ボニートロッサⅠ205")

    def test_building_level_drops_the_room_number(self):
        # Room 205 not listed; 206 matches on size, price and layout.
        m = pm.match(_ref(), [_prop(1, room="206")])
        self.assertIsNotNone(m)
        self.assertFalse(m.room_level)
        self.assertEqual(m.display_name, "ボニートロッサⅠ")


class TestRefusesWeakEvidence(unittest.TestCase):
    """Describing the wrong flat is worse than saying nothing."""

    def test_room_number_alone_is_not_enough(self):
        far = _prop(1, room="205", rent=120000, space=90.0, layout="3LDK",
                    city="名古屋市 東エリア", access="東山線「今池」徒歩5分")
        self.assertIsNone(pm.match(_ref(), [far]))

    def test_layout_alone_is_not_enough(self):
        self.assertIsNone(pm.match(
            _ref(), [_prop(1, room="999", rent=200000, space=12.0)]))

    def test_no_candidates_returns_none(self):
        self.assertIsNone(pm.match(_ref(), []))

    def test_reflection_without_attributes_returns_none(self):
        bare = Reflection(source="suumo", customer_name="x", customer_email="a@b.c",
                          property_name="どこかの建物101", property_url="",
                          inquiry_text="", extras={})
        self.assertIsNone(pm.match(bare, [_prop(1)]))

    def test_rent_outside_tolerance_is_rejected(self):
        self.assertIsNone(pm.match(
            _ref(rent="9.9万円"), [_prop(1, room="999", rent=65000, space=99.0)]))


class TestPreferences(unittest.TestCase):
    def test_vacant_preferred_when_evidence_is_equal(self):
        taken = _prop(1, room="206", vacant=False)
        free = _prop(2, room="206", vacant=True)
        m = pm.match(_ref(), [taken, free])
        self.assertTrue(m.prop.is_vacant)

    def test_room_match_beats_a_building_only_match(self):
        building_only = _prop(1, room="206")
        exact = _prop(2, room="205")
        m = pm.match(_ref(), [building_only, exact])
        self.assertEqual(m.prop.wp_id, 2)
        self.assertTrue(m.room_level)


if __name__ == "__main__":
    unittest.main()


class TestNotationDifferences(unittest.TestCase):
    """Same value, different notation on each side."""

    def test_wanroom_equals_1R(self):
        self.assertEqual(pm.layout_key("ワンルーム"), pm.layout_key("1R"))

    def test_layout_mismatch_no_longer_blocks_a_confirmed_room(self):
        # The client's case: SUUMO says ワンルーム, the site says 1R.
        m = pm.match(_ref(layout="ワンルーム"), [_prop(48329, layout="1R")])
        self.assertIsNotNone(m)
        self.assertEqual(m.prop.wp_id, 48329)
        self.assertIn("間取り", m.basis)

    def test_zero_padded_room_matches(self):
        # WESTIN上前津308 is registered as room 0308.
        self.assertEqual(pm.room_key("0308"), pm.room_key("308"))
        m = pm.match(_ref(name="ボニートロッサⅠ308"), [_prop(1, room="0308")])
        self.assertIsNotNone(m)
        self.assertTrue(m.room_level)

    def test_still_refuses_a_different_flat(self):
        # Right room number, everything else wrong — must not be accepted.
        wrong = _prop(1, room="205", rent=150000, space=95.0, layout="4LDK",
                      city="名古屋市 中心エリア", access="東山線「栄」徒歩3分")
        self.assertIsNone(pm.match(_ref(), [wrong]))
