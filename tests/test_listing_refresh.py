"""
Listings must not be frozen at whatever the site held when the service booted.

Live fault (2026-10-04): 「カーナ若宮306」 was answered 空室「なし」 and offered
alternative properties. Matching was correct — the room resolved to the right
listing — but the service had been running since 09-30 on the snapshot it loaded
then, in which that room was unavailable. The client had since made it available.
The same staleness hides newly published listings entirely.
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from src.core.inquiry_processor import InquiryProcessor
from src.core.models import Property
from src.integrations.wp_client import WordPressClient


def _prop(wp_id, vacant):
    return Property(
        wp_id=wp_id, name=f"n{wp_id}", url="u", rent=50000, management_fee=0,
        layout="1R", nearest_station="", train_line="", city="", walk_minutes=0,
        category=[], equipment=[], is_vacant=vacant, is_commission_free=False,
        area_sqm=20.0, building_type="", building_name=f"b{wp_id}",
        room_number="306")


def _client(loaded_at, properties):
    wp = WordPressClient.__new__(WordPressClient)
    wp._properties = properties
    wp._loaded_at = loaded_at
    return wp


class TestRefreshIfStale(unittest.TestCase):
    def test_a_fresh_snapshot_is_not_refetched(self):
        wp = _client(datetime.now() - timedelta(minutes=5), [_prop(1, False)])
        wp.load_all = MagicMock()
        self.assertFalse(wp.refresh_if_stale(30))
        wp.load_all.assert_not_called()

    def test_an_aged_snapshot_is_refetched(self):
        wp = _client(datetime.now() - timedelta(minutes=45), [_prop(1, False)])
        wp.load_all = MagicMock()
        self.assertTrue(wp.refresh_if_stale(30))
        wp.load_all.assert_called_once()

    def test_the_vacancy_change_becomes_visible(self):
        """The カーナ若宮306 case: unavailable in the snapshot, available now."""
        wp = _client(datetime.now() - timedelta(days=4), [_prop(1, False)])
        def _reload():
            wp._properties = [_prop(1, True)]
            wp._loaded_at = datetime.now()
        wp.load_all = MagicMock(side_effect=_reload)
        self.assertFalse(wp.properties[0].is_vacant)
        wp.refresh_if_stale(30)
        self.assertTrue(wp.properties[0].is_vacant)

    def test_never_loaded_refreshes_immediately(self):
        wp = _client(None, [])
        wp.load_all = MagicMock()
        self.assertTrue(wp.refresh_if_stale(30))

    def test_zero_disables_refreshing(self):
        wp = _client(datetime.now() - timedelta(days=30), [_prop(1, False)])
        wp.load_all = MagicMock()
        self.assertFalse(wp.refresh_if_stale(0))
        wp.load_all.assert_not_called()

    def test_a_failed_refresh_keeps_the_previous_listings(self):
        """An empty list is worse than a stale one — every lookup would miss."""
        kept = [_prop(1, True), _prop(2, False)]
        wp = _client(datetime.now() - timedelta(hours=2), kept)
        wp.load_all = MagicMock(side_effect=RuntimeError("WP down"))
        self.assertFalse(wp.refresh_if_stale(30))
        self.assertEqual(wp.properties, kept)

    def test_a_failed_refresh_is_retried_next_time(self):
        wp = _client(datetime.now() - timedelta(hours=2), [_prop(1, True)])
        wp.load_all = MagicMock(side_effect=RuntimeError("WP down"))
        wp.refresh_if_stale(30)
        wp.refresh_if_stale(30)
        self.assertEqual(wp.load_all.call_count, 2)


class TestScorerFollowsTheRefresh(unittest.TestCase):
    """Alternatives must not be drawn from the startup snapshot."""

    def _proc(self, refreshed):
        p = InquiryProcessor.__new__(InquiryProcessor)
        p._wp = MagicMock()
        p._wp.refresh_if_stale.return_value = refreshed
        p._wp.properties = [_prop(9, True)]
        p._scorer = MagicMock()
        p._wp_refresh_minutes = 30
        return p

    def test_scorer_is_rebuilt_when_listings_reload(self):
        p = self._proc(True)
        p._refresh_listings()
        p._scorer.reload.assert_called_once_with(p._wp.properties)

    def test_scorer_is_left_alone_when_nothing_reloaded(self):
        p = self._proc(False)
        p._refresh_listings()
        p._scorer.reload.assert_not_called()

    def test_a_refresh_failure_does_not_abort_the_cycle(self):
        p = self._proc(True)
        p._wp.refresh_if_stale.side_effect = RuntimeError("boom")
        p._refresh_listings()          # must not raise
        p._scorer.reload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
