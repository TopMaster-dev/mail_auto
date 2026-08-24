"""
The NG word definition (config/ng_words.yaml) against the client's spec.

Two rules here are easy to "fix" wrongly later, so they are pinned:
  * attribute words stay out of the literal list (spec §11-3)
  * words appearing in the client's own fixed template stay out, or every
    draft would be flagged forever
"""
import io
import unittest
from pathlib import Path

import yaml

from src.email_builder import assembler
from load_ng_words import load_definition

_DEF = Path(__file__).parent.parent / "config" / "ng_words.yaml"
_DATA = yaml.safe_load(io.open(_DEF, encoding="utf-8"))
_ALL = load_definition()
_WORDS = {w for w, _, _ in _ALL}
_ALWAYS = {w for w, _, st in _ALL if st == "初回から"}
_FOLLOWUP_ONLY = {w for w, _, st in _ALL if st == "2通目以降"}

# Fixed text the client supplied — the AI never writes it, and it must never
# be able to trip the NG scan.
_FIXED_TEXT = "\n".join([
    assembler._INITIAL_COST_NOTE,
    assembler._CTA_COMMON,
    assembler._SCHEDULE_TEMPLATE,
    assembler._SIGNATURE,
    assembler._LEAD_TAKEN,
    assembler._LEAD_UNIDENTIFIED,
])


class TestCoverage(unittest.TestCase):
    def test_every_category_from_the_spec_is_present(self):
        for category in ("クレーム系", "契約・法的関連", "審査・保証会社関連",
                         "修繕・設備トラブル", "返金・金銭関連", "緊急性", "個人情報"):
            self.assertIn(category, _DATA["categories"], category)

    def test_substantially_more_than_the_ten_originally_loaded(self):
        self.assertGreater(len(_WORDS), 100)

    def test_no_duplicates(self):
        words = [w for w, _, _ in _ALL]
        self.assertEqual(len(words), len(set(words)))

    def test_key_risk_words_present(self):
        for w in ("クレーム", "弁護士", "訴訟", "解約", "審査", "水漏れ",
                  "返金", "至急", "マイナンバー", "自己破産"):
            self.assertIn(w, _WORDS, w)


class TestAttributeWordsExcluded(unittest.TestCase):
    """§11-3: an attribute word alone must not stop a mail."""

    def test_protected_attributes_are_not_literal_ng_words(self):
        for w in ("外国籍", "高齢者", "生活保護", "無職", "フリーター",
                  "水商売", "夜職", "年金"):
            self.assertNotIn(w, _WORDS, f"{w} は文脈判定で扱うべき語です")

    def test_exclusion_is_documented(self):
        excluded = {w for e in _DATA["excluded"] for w in e["words"]}
        self.assertIn("外国籍", excluded)
        for entry in _DATA["excluded"]:
            self.assertTrue(entry["reason"].strip(), "除外理由が未記載")


class TestFollowupOnlyStaging(unittest.TestCase):
    """Cost words apply from the 2nd mail onward, not the 1st (client, 2026-08-21)."""

    def test_cost_words_are_followup_only(self):
        for w in ("初期費用", "仲介手数料", "礼金", "敷金", "保証料",
                  "火災保険料", "鍵交換費用", "日割り"):
            self.assertIn(w, _FOLLOWUP_ONLY, w)
            self.assertNotIn(w, _ALWAYS, w)

    def test_dispute_words_still_apply_from_the_first_mail(self):
        for w in ("請求ミス", "過剰請求", "ぼったくり", "値下げ", "家賃交渉", "高すぎる"):
            self.assertIn(w, _ALWAYS, w)

    def test_hayaku_is_now_included(self):
        # Client asked to keep 早く as 要確認 for now, reviewable later.
        self.assertIn("早く", _ALWAYS)


class TestNoAlwaysOnWordCollidesWithFixedText(unittest.TestCase):
    """A first-mail word inside the client's own template would deadlock sending.

    The cost words deliberately DO appear there, which is exactly why they are
    staged to the follow-up and why the draft scan reads only AI-written text.
    """

    def test_no_always_on_word_appears_in_the_fixed_template(self):
        colliding = sorted(w for w in _ALWAYS if w in _FIXED_TEXT)
        self.assertEqual(colliding, [],
                         f"固定文に含まれる語は初回から適用できません: {colliding}")

    def test_cost_words_are_in_the_template_hence_staged(self):
        self.assertIn("初期費用", _FIXED_TEXT)
        self.assertIn("初期費用", _FOLLOWUP_ONLY)


if __name__ == "__main__":
    unittest.main()
