"""Geographic proximity, and the guard that keeps suggestions local."""
import unittest

from src.core.models import Property
from src.matching import area
from src.matching.property_scorer import PropertyScorer


def _prop(wp_id, name, city, rent=70000, layout="2LDK", line="名鉄線",
          station="", vacant=True):
    return Property(
        wp_id=wp_id, name=name, url=f"https://rentmagazine.jp/estate/{wp_id}",
        rent=rent, management_fee=5000, layout=layout, nearest_station=station,
        train_line=line, city=city, walk_minutes=8, category=["ペット相談"],
        equipment=["オートロック"], is_vacant=vacant, is_commission_free=False,
        area_sqm=55.0, building_type="マンション")


class TestDistanceTier(unittest.TestCase):
    def test_same_area(self):
        self.assertEqual(area.distance_tier("岡崎市", "岡崎市"), area.SAME)

    def test_bordering_municipalities(self):
        self.assertEqual(area.distance_tier("岡崎市", "安城市"), area.ADJACENT)
        self.assertEqual(area.distance_tier("安城市", "岡崎市"), area.ADJACENT)

    def test_nagoya_subareas_count_as_one_city(self):
        self.assertEqual(
            area.distance_tier("名古屋市 東エリア", "名古屋市 西エリア"), area.ADJACENT)

    def test_okazaki_to_nagoya_is_far(self):
        # The exact complaint: ~35 km apart, different 生活圏.
        self.assertEqual(area.distance_tier("岡崎市", "名古屋市 東エリア"), area.FAR)
        self.assertEqual(area.distance_tier("岡崎市", "名古屋市 中心エリア"), area.FAR)

    def test_same_region_but_not_bordering(self):
        self.assertEqual(area.distance_tier("岡崎市", "刈谷市"), area.SAME_REGION)

    def test_unknown_bucket_is_neutral(self):
        self.assertEqual(area.distance_tier("岡崎市", area.UNKNOWN), area.SAME_REGION)

    def test_missing_value_is_neutral(self):
        self.assertEqual(area.distance_tier("", "岡崎市"), area.SAME_REGION)


class TestCityFromAddress(unittest.TestCase):
    def test_extracts_city(self):
        self.assertEqual(area.city_from_address("愛知県安城市東新町"), "安城市")

    def test_handles_ward(self):
        self.assertEqual(area.city_from_address("愛知県名古屋市瑞穂区惣作町2"), "名古屋市")

    def test_blank(self):
        self.assertEqual(area.city_from_address(""), "")


class TestAreaGuard(unittest.TestCase):
    def setUp(self):
        # Far pool is deliberately more attractive on rent and layout.
        self.props = [
            _prop(1, "岡崎A", "岡崎市", rent=95000, layout="1K"),
            _prop(2, "岡崎B", "岡崎市", rent=98000, layout="1K"),
            _prop(3, "安城A", "安城市", rent=99000, layout="1K"),
            _prop(10, "名古屋A", "名古屋市 東エリア", rent=70000, layout="2LDK"),
            _prop(11, "名古屋B", "名古屋市 西エリア", rent=70000, layout="2LDK"),
            _prop(12, "名古屋C", "名古屋市 中心エリア", rent=70000, layout="2LDK"),
        ]
        self.scorer = PropertyScorer(self.props)

    def test_okazaki_inquiry_stays_local(self):
        inquiry = _prop(99, "問い合わせ物件", "岡崎市", rent=70000, layout="2LDK")
        picked = self.scorer.find_alternatives(inquiry, top_n=3)
        cities = {p.city for p in picked}
        self.assertNotIn("名古屋市 東エリア", cities)
        self.assertTrue(cities <= {"岡崎市", "安城市"}, cities)

    def test_widens_when_nothing_is_near(self):
        # Nothing in or beside 半田市 — must still return suggestions.
        inquiry = _prop(99, "問い合わせ物件", "半田市", rent=70000, layout="2LDK")
        self.assertEqual(len(self.scorer.find_alternatives(inquiry, top_n=3)), 3)

    def test_no_reference_city_does_not_restrict(self):
        inquiry = _prop(99, "不明", "", rent=70000, layout="2LDK")
        self.assertEqual(len(self.scorer.find_alternatives(inquiry, top_n=3)), 3)


if __name__ == "__main__":
    unittest.main()
