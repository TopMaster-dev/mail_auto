"""Unit tests for PropertyScorer."""
import unittest

from src.core.models import Property
from src.matching.property_scorer import PropertyScorer


def _prop(wp_id: int, name: str, station: str, line: str, city: str,
          rent: int = 80000, layout: str = "1LDK",
          category: list | None = None, equipment: list | None = None,
          vacant: bool = True) -> Property:
    return Property(
        wp_id=wp_id, name=name, url=f"https://rentmagazine.jp/{wp_id}",
        rent=rent, management_fee=5000, layout=layout,
        nearest_station=station, train_line=line, city=city,
        walk_minutes=5, category=category or [], equipment=equipment or [],
        is_vacant=vacant, is_commission_free=False,
        area_sqm=40.0, building_type="マンション",
    )


class TestPropertyScorer(unittest.TestCase):
    def setUp(self):
        self.inquiry_prop = _prop(0, "問い合わせ物件", "知立駅", "名鉄三河線", "知立市",
                                  rent=80000, layout="1LDK", vacant=False)
        self.props = [
            _prop(1, "同駅物件", "知立駅", "名鉄三河線", "知立市", rent=82000),
            _prop(2, "同沿線物件", "刈谷駅", "名鉄三河線", "刈谷市", rent=75000),
            _prop(3, "同市物件", "牛田駅", "名鉄名古屋線", "知立市", rent=78000),
            _prop(4, "遠方物件", "栄駅", "名城線", "名古屋市中区", rent=120000),
            _prop(5, "満室物件", "知立駅", "名鉄三河線", "知立市", vacant=False),  # not vacant
        ]
        self.scorer = PropertyScorer(self.props)

    def test_same_station_ranked_first(self):
        results = self.scorer.find_alternatives(self.inquiry_prop, top_n=3)
        self.assertEqual(results[0].name, "同駅物件")

    def test_vacant_only(self):
        results = self.scorer.find_alternatives(self.inquiry_prop, top_n=5)
        self.assertTrue(all(p.is_vacant for p in results))
        self.assertNotIn("満室物件", [p.name for p in results])

    def test_inquiry_property_excluded(self):
        results = self.scorer.find_alternatives(self.inquiry_prop, top_n=5)
        ids = [p.wp_id for p in results]
        self.assertNotIn(0, ids)

    def test_top_n_respected(self):
        results = self.scorer.find_alternatives(self.inquiry_prop, top_n=2)
        self.assertLessEqual(len(results), 2)

    def test_no_vacant_returns_empty(self):
        all_full = [_prop(i, f"p{i}", "X駅", "Y線", "Z市", vacant=False)
                    for i in range(5)]
        scorer = PropertyScorer(all_full)
        results = scorer.find_alternatives(self.inquiry_prop)
        self.assertEqual(results, [])

    def test_category_match_boosts_score(self):
        designer = _prop(10, "デザイナーズ物件", "刈谷駅", "名鉄三河線", "刈谷市",
                         category=["デザイナーズ"])
        normal = _prop(11, "普通物件", "刈谷駅", "名鉄三河線", "刈谷市")
        scorer = PropertyScorer([designer, normal])
        inquiry = _prop(0, "問い合わせ", "知立駅", "名鉄三河線", "知立市",
                        category=["デザイナーズ"], vacant=False)
        results = scorer.find_alternatives(inquiry, top_n=2)
        self.assertEqual(results[0].name, "デザイナーズ物件")

    def test_reload_updates_data(self):
        new_props = [_prop(99, "新物件", "知立駅", "名鉄三河線", "知立市")]
        self.scorer.reload(new_props)
        results = self.scorer.find_alternatives(self.inquiry_prop)
        self.assertEqual(results[0].name, "新物件")


if __name__ == "__main__":
    unittest.main()
