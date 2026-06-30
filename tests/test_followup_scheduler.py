"""Unit tests for the follow-up scheduler logic."""
import unittest
from unittest.mock import MagicMock

from src.core.models import Property
from src.scheduling.followup_scheduler import FollowupScheduler, decide_followup

_STEPS = [2, 3]
_MAX = 2


def _prop(vacant=True) -> Property:
    return Property(
        wp_id=1, name="テスト物件", url="https://rentmagazine.jp/estate/1",
        rent=80000, management_fee=5000, layout="1LDK", nearest_station="知立駅",
        train_line="名鉄三河線", city="知立市", walk_minutes=5,
        category=["デザイナーズ"], equipment=["追い焚き"],
        is_vacant=vacant, is_commission_free=False, area_sqm=42.0,
        building_type="マンション",
    )


class TestDecideFollowup(unittest.TestCase):
    def test_first_followup_is_second_mail(self):
        self.assertEqual(decide_followup(1, _STEPS, _MAX), ("2nd", 2, 3, "追客中"))

    def test_second_followup_is_third_mail_and_completes(self):
        self.assertEqual(decide_followup(2, _STEPS, _MAX), ("3rd", 3, None, "追客完了"))

    def test_completed_returns_none(self):
        self.assertIsNone(decide_followup(3, _STEPS, _MAX))

    def test_not_yet_sent_returns_none(self):
        self.assertIsNone(decide_followup(0, _STEPS, _MAX))


class TestSendFollowup(unittest.TestCase):
    def _scheduler(self):
        gen = MagicMock()
        gen.generate_visit_invitation.return_value = "費用面もご相談いただけます。"
        gen.generate_property_intro.return_value = "素敵なお部屋です。"
        gen.generate_alt_intro.return_value = "こちらもおすすめです。"
        scorer = MagicMock()
        scorer.find_alternatives.return_value = []
        wp = MagicMock()
        wp.get_property_by_url.return_value = _prop(vacant=True)
        gmail = MagicMock()
        gmail.send.return_value = "<sent-mid@rentmagazine.jp>"
        sheets = MagicMock()
        return FollowupScheduler(
            sheets=sheets, gmail=gmail, wp=wp, generator=gen, scorer=scorer,
            company={"staff_name": "担当者"}, cfg={"steps": [{"days": 2}, {"days": 3}],
                                                  "max_followups": 2},
        ), sheets, gmail

    def _record(self, count):
        return {
            "ID": "abc123", "顧客名": "太郎", "メールアドレス": "taro@example.com",
            "問い合わせ物件": "テスト物件", "物件URL": "https://rentmagazine.jp/estate/1",
            "空室有無": "あり", "追客ステータス": "追客中", "追客回数": str(count),
            "次回追客予定日": "2026-06-01 10:00:00",
        }

    def test_sends_second_mail_and_schedules_third(self):
        sched, sheets, gmail = self._scheduler()
        sched._send_followup(self._record(count=1))
        gmail.send.assert_called_once()
        # advance_followup(id, new_count=2, next_at!=None, status="追客中")
        args = sheets.advance_followup.call_args[0]
        self.assertEqual(args[0], "abc123")
        self.assertEqual(args[1], 2)
        self.assertIsNotNone(args[2])
        self.assertEqual(args[3], "追客中")
        sheets.write_followup_log.assert_called_once_with("abc123", "2nd", "送信")

    def test_sends_third_mail_and_completes(self):
        sched, sheets, gmail = self._scheduler()
        sched._send_followup(self._record(count=2))
        gmail.send.assert_called_once()
        args = sheets.advance_followup.call_args[0]
        self.assertEqual(args[1], 3)
        self.assertIsNone(args[2])           # no next follow-up
        self.assertEqual(args[3], "追客完了")
        sheets.write_followup_log.assert_called_once_with("abc123", "3rd", "送信")

    def test_already_completed_does_not_send(self):
        sched, sheets, gmail = self._scheduler()
        sched._send_followup(self._record(count=3))
        gmail.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
