"""
Vacancy stated in a reply must be true when the reply is written.

The bulk snapshot (refresh_minutes, default 30) is fine for matching across
~1,900 listings, but a reply says 「ご紹介できない状況です」 about one specific room.
That claim is checked against WordPress at the moment of writing — one request,
~1.8s, against a reply already spending tens of seconds in the Claude API.

Client asked (2026-10-05) to remove the lag entirely for vacancy.
"""
import unittest
from unittest.mock import MagicMock

from src.core.inquiry_processor import InquiryProcessor
from src.core.models import Property


def _prop(wp_id, vacant, name="b"):
    return Property(
        wp_id=wp_id, name=name, url="u", rent=50000, management_fee=0, layout="1R",
        nearest_station="", train_line="", city="", walk_minutes=0, category=[],
        equipment=[], is_vacant=vacant, is_commission_free=False, area_sqm=20.0,
        building_type="", building_name=name, room_number="306")


class TestEnquiredRoomIsRechecked(unittest.TestCase):
    def setUp(self):
        self.p = InquiryProcessor.__new__(InquiryProcessor)
        self.p._wp = MagicMock()
        self.p._sheets = MagicMock()
        self.inq = MagicMock()
        self.inq.id = "i1"
        self.inq.inquiry_property_url = ""
        self.inq.inquiry_property_name = "カーナ若宮306"

    def _lookup(self, snapshot, live):
        self.p._wp.get_property_by_url.return_value = None
        self.p._wp.resolve_by_formal_name.return_value = (snapshot, "カーナ若宮306")
        self.p._wp.refetch_one.return_value = live
        return self.p._lookup_property(self.inq, None)

    def test_the_live_answer_wins_over_the_snapshot(self):
        """The カーナ若宮306 case: stale said taken, live says available."""
        _, vacant = self._lookup(_prop(4801, False), _prop(4801, True))
        self.assertTrue(vacant)

    def test_a_room_let_since_the_snapshot_is_reported_as_taken(self):
        _, vacant = self._lookup(_prop(4801, True), _prop(4801, False))
        self.assertFalse(vacant)

    def test_the_recheck_targets_the_matched_listing(self):
        self._lookup(_prop(4801, False), _prop(4801, True))
        self.p._wp.refetch_one.assert_called_once_with(4801)

    def test_an_unreadable_listing_falls_back_to_the_snapshot(self):
        """WordPress being down must not block the reply."""
        prop, vacant = self._lookup(_prop(4801, True), None)
        self.assertTrue(vacant)
        self.assertEqual(prop.wp_id, 4801)

    def test_fresh_data_replaces_the_whole_record(self):
        """Not just vacancy — rent and layout may have changed too."""
        live = _prop(4801, True)
        live.rent = 61000
        prop, _ = self._lookup(_prop(4801, True), live)
        self.assertEqual(prop.rent, 61000)


class TestAlternativesAreConfirmed(unittest.TestCase):
    def setUp(self):
        self.p = InquiryProcessor.__new__(InquiryProcessor)
        self.p._wp = MagicMock()

    def test_a_let_suggestion_is_dropped(self):
        self.p._wp.refetch_one.side_effect = lambda i: _prop(i, i != 2)
        kept = self.p._confirmed_vacant([_prop(1, True), _prop(2, True), _prop(3, True)])
        self.assertEqual([p.wp_id for p in kept], [1, 3])

    def test_all_still_vacant_are_kept(self):
        self.p._wp.refetch_one.side_effect = lambda i: _prop(i, True)
        kept = self.p._confirmed_vacant([_prop(1, True), _prop(2, True)])
        self.assertEqual(len(kept), 2)

    def test_unreadable_suggestions_are_kept_not_discarded(self):
        """A WordPress hiccup must not empty the recommendations."""
        self.p._wp.refetch_one.return_value = None
        kept = self.p._confirmed_vacant([_prop(1, True), _prop(2, True)])
        self.assertEqual(len(kept), 2)

    def test_nothing_to_check_costs_no_requests(self):
        self.assertEqual(self.p._confirmed_vacant([]), [])
        self.p._wp.refetch_one.assert_not_called()


class TestRefetchOne(unittest.TestCase):
    def setUp(self):
        from src.integrations.wp_client import WordPressClient
        self.wp = WordPressClient.__new__(WordPressClient)
        self.wp._fcfg = {"vacancy_status": "display_none",
                         "vacancy_available_value": ""}
        self.wp._tcfg = {}
        self.wp._term_cache = {}

    def test_an_empty_id_makes_no_request(self):
        self.wp._get = MagicMock()
        self.assertIsNone(self.wp.refetch_one(0))
        self.wp._get.assert_not_called()

    def test_a_network_failure_returns_none(self):
        self.wp._get = MagicMock(side_effect=RuntimeError("down"))
        self.assertIsNone(self.wp.refetch_one(4801))

    def test_an_unexpected_payload_returns_none(self):
        self.wp._get = MagicMock(return_value=[])      # 400 path returns []
        self.assertIsNone(self.wp.refetch_one(4801))

    def test_a_listed_property_reads_as_vacant(self):
        self.wp._get = MagicMock(return_value={
            "id": 4801, "title": {"rendered": "日常から少し離れて"},
            "link": "https://x/", "acf": {"display_none": ""}})
        p = self.wp.refetch_one(4801)
        self.assertIsNotNone(p)
        self.assertTrue(p.is_vacant)


if __name__ == "__main__":
    unittest.main()
