"""
WordPress payload normalisation. ACF fields arrive as a list, a scalar or null
depending on registration, and one null used to abort the whole startup load.
"""
import unittest

from src.integrations.wp_client import (
    WordPressClient, _first_int, _first_str, _int, _str_list,
)

_FIELDS = {
    "vacancy_status": "display_none", "vacancy_available_value": "",
    "rent": "rent", "management_fee": "administrative", "area_sqm": "space",
    "walk_minutes": "walktime", "layout": "list_of_rooms", "equipment": "equipment",
    "commission_free_term": "仲介手数料０円",
}
_TAXONOMIES = {
    "train_line": "train", "area": "area",
    "category": ["condition1", "condition2"], "building_type": "building",
}


def _client() -> WordPressClient:
    # No request is made — only the normalisation path is exercised.
    return WordPressClient("https://rentmagazine.jp", "", "", 100, _FIELDS, _TAXONOMIES)


class TestShapeHelpers(unittest.TestCase):
    def test_first_str_handles_list_scalar_and_null(self):
        self.assertEqual(_first_str(["1LDK"]), "1LDK")
        self.assertEqual(_first_str("2DK"), "2DK")
        self.assertEqual(_first_str(None), "")
        self.assertEqual(_first_str([]), "")

    def test_first_int_handles_list_scalar_and_null(self):
        self.assertEqual(_first_int(["10", "15", "20"]), 10)
        self.assertEqual(_first_int("7"), 7)
        self.assertEqual(_first_int(None), 0)
        self.assertEqual(_first_int([]), 0)

    def test_str_list_handles_list_csv_and_null(self):
        self.assertEqual(_str_list(["オートロック", "宅配BOX"]), ["オートロック", "宅配BOX"])
        self.assertEqual(_str_list("オートロック, 宅配BOX"), ["オートロック", "宅配BOX"])
        self.assertEqual(_str_list(None), [])

    def test_int_strips_japanese_currency_formatting(self):
        self.assertEqual(_int("45,000円"), 45000)
        self.assertEqual(_int(None), 0)


class TestToProperty(unittest.TestCase):
    def setUp(self):
        self.client = _client()
        self.base = {"id": 1, "title": {"rendered": "物件A"}, "link": "https://x/1"}

    def test_null_title_and_link_do_not_raise(self):
        prop = self.client._to_property({"id": 1, "title": None, "link": None, "acf": {}})
        self.assertEqual(prop.name, "")
        self.assertEqual(prop.url, "")

    def test_null_acf_values_fall_back_to_defaults(self):
        raw = {**self.base, "acf": {"list_of_rooms": None, "walktime": None,
                                    "equipment": None, "rent": None,
                                    "display_none": ""}}
        prop = self.client._to_property(raw)
        self.assertEqual(prop.layout, "")   # not the literal string "None"
        self.assertEqual(prop.rent, 0)
        self.assertEqual(prop.equipment, [])
        self.assertEqual(prop.walk_minutes, 0)

    def test_normal_payload_is_read_correctly(self):
        raw = {**self.base, "acf": {"list_of_rooms": ["1LDK"], "walktime": ["10", "15"],
                                    "equipment": ["オートロック"], "rent": "82,000",
                                    "administrative": 5000, "space": "42.5",
                                    "display_none": ""}}
        prop = self.client._to_property(raw)
        self.assertEqual(prop.layout, "1LDK")
        self.assertEqual(prop.walk_minutes, 10)
        self.assertEqual(prop.rent, 82000)
        self.assertEqual(prop.management_fee, 5000)
        self.assertEqual(prop.area_sqm, 42.5)
        self.assertEqual(prop.equipment, ["オートロック"])
        self.assertTrue(prop.is_vacant)

    def test_vacancy_flag_follows_display_none(self):
        vacant = self.client._to_property({**self.base, "acf": {"display_none": ""}})
        listed_off = self.client._to_property({**self.base, "acf": {"display_none": "none"}})
        self.assertTrue(vacant.is_vacant)
        self.assertFalse(listed_off.is_vacant)

    def test_missing_acf_block_is_not_vacant(self):
        # No acf at all → vacancy cannot be confirmed, so do not claim it.
        self.assertFalse(self.client._to_property(self.base).is_vacant)


if __name__ == "__main__":
    unittest.main()
