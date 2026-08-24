"""
Stage-scoped NG words and the complaint judgment (client, 2026-08-21).

Rule: a cost question on its own is a normal first enquiry and must not be
held. The same words wrapped in dissatisfaction must be held from the start.
"""
import unittest
from unittest.mock import MagicMock

from src.ai.content_checker import (
    STAGE_ALL, STAGE_FOLLOWUP, STAGE_FOLLOWUP_ONLY, STAGE_INITIAL, ContentChecker,
)

_WORDS = [
    {"ワード": "クレーム", "カテゴリ": "クレーム系", "適用段階": STAGE_ALL},
    {"ワード": "初期費用", "カテゴリ": "返金・金銭関連", "適用段階": STAGE_FOLLOWUP_ONLY},
    {"ワード": "仲介手数料", "カテゴリ": "返金・金銭関連", "適用段階": STAGE_FOLLOWUP_ONLY},
]


def _checker(discriminatory=False, complaint=False, reason="", complaint_reason=""):
    client = MagicMock()
    payload = ('{"discriminatory": %s, "reason": "%s", '
               '"complaint": %s, "complaint_reason": "%s"}'
               % (str(discriminatory).lower(), reason,
                  str(complaint).lower(), complaint_reason))
    client.messages.create.return_value = MagicMock(
        content=[MagicMock(text=payload)])
    return ContentChecker(ng_words=_WORDS, client=client, model="test")


class TestStageScoping(unittest.TestCase):
    def test_cost_word_passes_on_the_first_mail(self):
        r = _checker().check("初期費用はいくらですか？", stage=STAGE_INITIAL)
        self.assertTrue(r.is_clean)
        self.assertEqual(r.ng_hits, [])

    def test_same_cost_word_is_held_from_the_follow_up(self):
        r = _checker().check("初期費用はいくらですか？", stage=STAGE_FOLLOWUP)
        self.assertFalse(r.is_clean)
        self.assertEqual([h.word for h in r.ng_hits], ["初期費用"])

    def test_always_on_word_is_held_at_both_stages(self):
        for stage in (STAGE_INITIAL, STAGE_FOLLOWUP):
            r = _checker().check("クレームを入れます", stage=stage)
            self.assertFalse(r.is_clean, stage)
            self.assertIn("クレーム", [h.word for h in r.ng_hits])

    def test_initial_is_the_default_stage(self):
        self.assertTrue(_checker().check("初期費用について").is_clean)

    def test_staged_words_never_apply_to_our_own_copy(self):
        # Roughly half of generated intros mention 仲介手数料 because the listing
        # really is commission-free. Screening our own copy against the staged
        # cost words would block follow-ups the client wants sent automatically.
        r = _checker().check_generated(["仲介手数料無料の魅力的なお部屋です"])
        self.assertTrue(r.is_clean)
        self.assertEqual(r.ng_hits, [])

    def test_generated_text_still_screens_always_on_words(self):
        r = _checker().check_generated(["クレームが多い物件です"])
        self.assertFalse(r.is_clean)
        self.assertIn("クレーム", [h.word for h in r.ng_hits])


class TestComplaintJudgment(unittest.TestCase):
    def test_plain_cost_question_is_not_a_complaint(self):
        r = _checker(complaint=False).check("初期費用はいくらですか？")
        self.assertFalse(r.complaint)
        self.assertTrue(r.is_clean)

    def test_cost_question_with_dissatisfaction_is_held_on_the_first_mail(self):
        # The client's own example. Held by context, not by the word 初期費用.
        r = _checker(complaint=True, complaint_reason="費用への強い不満").check(
            "初期費用が高すぎる。納得できません", stage=STAGE_INITIAL)
        self.assertTrue(r.complaint)
        self.assertFalse(r.is_clean)
        self.assertEqual(r.complaint_reason, "費用への強い不満")

    def test_complaint_alone_holds_even_with_no_ng_word(self):
        r = _checker(complaint=True, complaint_reason="対応への不満").check("対応が遅くて困ります")
        self.assertFalse(r.is_clean)
        self.assertEqual(r.ng_hits, [])

    def test_discrimination_and_complaint_are_reported_separately(self):
        r = _checker(discriminatory=True, reason="属性で入居制限",
                     complaint=True, complaint_reason="強い不満").check("テキスト")
        self.assertTrue(r.discriminatory)
        self.assertTrue(r.complaint)
        self.assertEqual(r.discriminatory_reason, "属性で入居制限")
        self.assertEqual(r.complaint_reason, "強い不満")

    def test_one_api_call_covers_both_judgments(self):
        c = _checker()
        c.check("本文")
        self.assertEqual(c._client.messages.create.call_count, 1)

    def test_api_failure_still_fails_safe(self):
        client = MagicMock()
        client.messages.create.side_effect = Exception("boom")
        c = ContentChecker(ng_words=_WORDS, client=client, model="test")
        r = c.check("問題のない本文")
        self.assertTrue(r.discriminatory)
        self.assertFalse(r.is_clean)


if __name__ == "__main__":
    unittest.main()
