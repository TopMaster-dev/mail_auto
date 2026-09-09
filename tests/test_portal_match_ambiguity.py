"""
A portal match must never be decided by luck.

Guards a latent hazard rather than a fault seen in production: パストラーレ富士松_307
is registered with 部屋番号 107 despite its name ending 307, so a 107 reflection can
agree with it on room number through data error alone. Where two *buildings* agree
equally well the winner would be whichever WordPress happened to return first, and
describing the wrong flat to a customer is worse than staying vague — so an equal
tie now yields no match at all.

The listings below are deliberately sparse (no 間取り/専有面積) to force the tie the
guard exists for; the real records for these buildings carry both fields.
"""
import unittest

from src.core.models import Property
from src.matching import portal_matcher


class _Reflection:
    def __init__(self, property_name, extras):
        self.property_name = property_name
        self.extras = extras
        self.source = "suumo"


def _prop(building_name, room, rent, vacant=False, area=0.0, layout="",
          access='名鉄本線「牛田」徒歩20分', city="愛知 その他エリア"):
    return Property(
        wp_id=1, name=building_name, url="u", rent=rent, management_fee=0,
        layout=layout, nearest_station="", train_line="名鉄本線", city=city,
        walk_minutes=20, category=[], equipment=[], is_vacant=vacant,
        is_commission_free=False, area_sqm=area, building_type="",
        building_name=building_name, room_number=room, address="", access=access)


# The reflection exactly as SUUMO sent it on 2026-09-07.
BONITOROSA = _Reflection("ボニートロッサI107", {
    "最寄り駅": "名鉄名古屋本線/牛田",
    "所在地": "愛知県知立市八ツ田町山畔",
    "賃料": "6.4万円",
    "間取り": "ワンルーム",
    "専有面積": "50平米",
})

CORRECT = _prop("bonitorosa Ⅰ・Ⅱ", "107", 64000)
IMPOSTOR = _prop("パストラーレ富士松_307", "107", 65000, vacant=True)


class TestRentToleranceDiscriminates(unittest.TestCase):
    def test_a_1000_yen_gap_is_not_the_same_rent(self):
        """±3000 made rent useless as a signal; SUUMO only rounds by ±500."""
        want = {"room": "", "layout": "", "area": 0.0, "rent": 64000,
                "stations": [], "city": ""}
        self.assertNotIn("賃料", portal_matcher._signals(_prop("x", "1", 65000), want))

    def test_rounding_slack_is_still_tolerated(self):
        want = {"room": "", "layout": "", "area": 0.0, "rent": 64000,
                "stations": [], "city": ""}
        self.assertIn("賃料", portal_matcher._signals(_prop("x", "1", 63500), want))


class TestAmbiguityIsNotAGuess(unittest.TestCase):
    def test_the_tighter_rent_window_separates_them(self):
        m = portal_matcher.match(BONITOROSA, [IMPOSTOR, CORRECT])
        self.assertIsNotNone(m)
        self.assertEqual(m.prop.building_name, "bonitorosa Ⅰ・Ⅱ")

    def test_load_order_does_not_change_the_answer(self):
        a = portal_matcher.match(BONITOROSA, [IMPOSTOR, CORRECT])
        b = portal_matcher.match(BONITOROSA, [CORRECT, IMPOSTOR])
        self.assertEqual(a.prop.building_name, b.prop.building_name)

    def test_a_genuine_tie_between_buildings_returns_nothing(self):
        """Same rent, same room, different buildings — unresolvable."""
        twin = _prop("パストラーレ富士松_307", "107", 64000, vacant=True)
        self.assertIsNone(portal_matcher.match(BONITOROSA, [twin, CORRECT]))

    def test_vacancy_never_breaks_a_tie_between_buildings(self):
        """A vacant wrong flat must not beat an occupied right one."""
        twin = _prop("まったく別の建物", "107", 64000, vacant=True)
        self.assertIsNone(portal_matcher.match(BONITOROSA, [twin, CORRECT]))

    def test_two_rooms_of_one_building_are_not_ambiguous(self):
        """オリーブ_201 / オリーブ_205 are one building, not two."""
        a = _prop("オリーブ_201", "201", 64000)
        b = _prop("オリーブ_205", "205", 64000, vacant=True)
        ref = _Reflection("オリーブ", {"賃料": "6.4万円", "間取り": "ワンルーム",
                                      "専有面積": "50平米",
                                      "最寄り駅": "名鉄本線/牛田"})
        m = portal_matcher.match(ref, [a, b])
        if m is not None:
            self.assertIn("オリーブ", m.prop.building_name)

    def test_building_key_ignores_the_room_suffix(self):
        self.assertEqual(portal_matcher._building_key(_prop("オリーブ_201", "201", 1)),
                         portal_matcher._building_key(_prop("オリーブ_205", "205", 1)))

    def test_building_key_keeps_genuinely_different_buildings_apart(self):
        self.assertNotEqual(
            portal_matcher._building_key(_prop("bonitorosa Ⅰ・Ⅱ", "107", 1)),
            portal_matcher._building_key(_prop("パストラーレ富士松_307", "107", 1)))


if __name__ == "__main__":
    unittest.main()
