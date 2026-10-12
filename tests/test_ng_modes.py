"""
強制停止 vs 文脈判定 (client approved 2026-09-11).

The NG list holds words that are ordinary in a genuine enquiry — 審査, 保証会社,
解約, 値下げ, 至急, 折り返し. Blocking on the word alone held routine questions for
a person, which the client saw in 要確認履歴: 「審査」 detected, AI verdict
「通常問い合わせ」, mail stopped anyway.

A 文脈判定 word is still recorded, so the operator can see it was present and how
the AI read it — it simply no longer stops the reply by itself. Blank means
強制停止, so an unedited sheet is exactly as strict as before.
"""
import unittest
from unittest.mock import MagicMock, patch

from src.ai.content_checker import (MODE_CONTEXT, MODE_FORCE, STAGE_ALL,
                                    ContentChecker)


def _checker(words, disc=False, complaint=False):
    c = ContentChecker.__new__(ContentChecker)
    c._client = MagicMock()
    c._model = "m"
    c._ng_words = []
    c.reload_ng_words(words)
    c._check_context = MagicMock(
        return_value=(disc, "差別理由" if disc else "",
                      complaint, "苦情理由" if complaint else ""))
    return c


def _word(w, mode="", stage=STAGE_ALL, category="テスト"):
    return {"ワード": w, "カテゴリ": category, "適用段階": stage, "判定方式": mode}


class TestForcedWordsStillBlock(unittest.TestCase):
    def test_a_forced_word_holds_the_mail(self):
        c = _checker([_word("弁護士", MODE_FORCE)])
        self.assertFalse(c.check("弁護士に相談します").is_clean)

    def test_an_unmarked_word_defaults_to_forced(self):
        """An unedited sheet must behave exactly as before the column existed."""
        c = _checker([{"ワード": "弁護士", "カテゴリ": "法的"}])
        result = c.check("弁護士に相談します")
        self.assertFalse(result.is_clean)
        self.assertEqual(result.ng_hits[0].mode, MODE_FORCE)

    def test_an_unrecognised_mode_value_is_treated_as_forced(self):
        c = _checker([_word("弁護士", "てきとう")])
        self.assertFalse(c.check("弁護士に相談します").is_clean)


class TestContextWordsNoLongerBlockAlone(unittest.TestCase):
    def test_a_routine_enquiry_mentioning_an_ng_word_goes_through(self):
        """The 審査 case the client found in 要確認履歴."""
        c = _checker([_word("審査", MODE_CONTEXT)])
        result = c.check("入居審査について教えてください")
        self.assertTrue(result.is_clean)

    def test_the_word_is_still_recorded_for_the_operator(self):
        c = _checker([_word("審査", MODE_CONTEXT)])
        result = c.check("入居審査について教えてください")
        self.assertEqual([h.word for h in result.ng_hits], ["審査"])
        self.assertEqual(result.ng_hits[0].mode, MODE_CONTEXT)

    def test_angry_context_still_blocks(self):
        """The AI's complaint judgement is unaffected by the mode."""
        c = _checker([_word("審査", MODE_CONTEXT)], complaint=True)
        self.assertFalse(c.check("審査が遅すぎる。納得できない").is_clean)

    def test_discriminatory_context_still_blocks(self):
        c = _checker([_word("審査", MODE_CONTEXT)], disc=True)
        self.assertFalse(c.check("外国籍の方は審査に通りません").is_clean)

    def test_a_forced_word_blocks_even_beside_context_words(self):
        c = _checker([_word("審査", MODE_CONTEXT), _word("訴訟", MODE_FORCE)])
        self.assertFalse(c.check("審査の件、訴訟も検討します").is_clean)


class TestModesCombineWithStages(unittest.TestCase):
    def test_a_followup_only_context_word_is_ignored_initially(self):
        from src.ai.content_checker import STAGE_FOLLOWUP_ONLY, STAGE_INITIAL
        c = _checker([_word("初期費用", MODE_CONTEXT, STAGE_FOLLOWUP_ONLY)])
        result = c.check("初期費用はいくらですか", stage=STAGE_INITIAL)
        self.assertTrue(result.is_clean)
        self.assertEqual(result.ng_hits, [])

    def test_a_followup_only_forced_word_blocks_at_the_followup_stage(self):
        from src.ai.content_checker import STAGE_FOLLOWUP, STAGE_FOLLOWUP_ONLY
        c = _checker([_word("初期費用", MODE_FORCE, STAGE_FOLLOWUP_ONLY)])
        self.assertFalse(c.check("初期費用の件", stage=STAGE_FOLLOWUP).is_clean)


class TestNothingElseChanged(unittest.TestCase):
    def test_clean_text_with_no_hits_passes(self):
        c = _checker([_word("弁護士", MODE_FORCE)])
        self.assertTrue(c.check("内見を希望します").is_clean)

    def test_context_judgements_alone_still_block_without_any_word(self):
        c = _checker([], complaint=True)
        self.assertFalse(c.check("対応が遅くて困っています").is_clean)


if __name__ == "__main__":
    unittest.main()
