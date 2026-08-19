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
_WORDS = {w for w, _ in load_definition()}

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
        words = [w for w, _ in load_definition()]
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


class TestNoWordCollidesWithFixedText(unittest.TestCase):
    """A word that appears in the client's own template would deadlock sending."""

    def test_no_definition_word_appears_in_the_fixed_template(self):
        colliding = sorted(w for w in _WORDS if w in _FIXED_TEXT)
        self.assertEqual(colliding, [],
                         f"固定文に含まれる語は使えません: {colliding}")

    def test_the_known_offenders_are_excluded(self):
        # These are exactly why the collision rule exists.
        self.assertIn("初期費用", _FIXED_TEXT)
        self.assertNotIn("初期費用", _WORDS)


if __name__ == "__main__":
    unittest.main()
