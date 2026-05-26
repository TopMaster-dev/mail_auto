"""
Unit tests for ContentChecker.
Discriminatory expression tests mock the Claude API call.
NG word scan tests run without any external dependency.
"""
import unittest
from unittest.mock import MagicMock, patch

from src.ai.content_checker import ContentChecker
from src.core.models import NGHit

_SAMPLE_NG_WORDS = [
    {"ワード": "クレーム", "カテゴリ": "クレーム系"},
    {"ワード": "弁護士", "カテゴリ": "契約・法的関連"},
    {"ワード": "水漏れ", "カテゴリ": "修繕・設備トラブル"},
    {"ワード": "返金", "カテゴリ": "返金・金銭関連"},
    {"ワード": "至急", "カテゴリ": "緊急性"},
]


def _make_checker(discriminatory: bool = False, reason: str = "") -> ContentChecker:
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text=f'{{"discriminatory": {str(discriminatory).lower()}, "reason": "{reason}"}}')]
    mock_client.messages.create.return_value = mock_resp
    return ContentChecker(ng_words=_SAMPLE_NG_WORDS, client=mock_client, model="test-model")


class TestNGWordScan(unittest.TestCase):
    def setUp(self):
        self.checker = _make_checker()

    def test_clean_text_passes(self):
        result = self.checker.check("お部屋を内見したいです。よろしくお願いします。")
        self.assertTrue(result.is_clean)
        self.assertEqual(result.ng_hits, [])

    def test_single_ng_word_detected(self):
        result = self.checker.check("クレームを入れたいのですが。")
        self.assertFalse(result.is_clean)
        self.assertEqual(len(result.ng_hits), 1)
        self.assertEqual(result.ng_hits[0].word, "クレーム")
        self.assertEqual(result.ng_hits[0].category, "クレーム系")

    def test_multiple_ng_words(self):
        result = self.checker.check("弁護士に相談して返金を求めます。")
        words = [h.word for h in result.ng_hits]
        self.assertIn("弁護士", words)
        self.assertIn("返金", words)

    def test_fullwidth_halfwidth_normalization(self):
        # "クレーム" in full-width should still match
        result = self.checker.check("クレームについて。")
        self.assertFalse(result.is_clean)

    def test_no_duplicate_hits(self):
        result = self.checker.check("クレームです。クレームを言います。")
        self.assertEqual(len([h for h in result.ng_hits if h.word == "クレーム"]), 1)


class TestDiscriminatoryCheck(unittest.TestCase):
    def test_discriminatory_expression_flagged(self):
        checker = _make_checker(discriminatory=True, reason="外国人不可の表現")
        result = checker.check("外国人不可です。")
        self.assertTrue(result.discriminatory)
        self.assertEqual(result.discriminatory_reason, "外国人不可の表現")
        self.assertFalse(result.is_clean)

    def test_inclusive_expression_passes(self):
        checker = _make_checker(discriminatory=False)
        result = checker.check("外国籍の方もご相談可能です。")
        self.assertFalse(result.discriminatory)
        self.assertTrue(result.is_clean)

    def test_api_error_defaults_to_review(self):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")
        checker = ContentChecker(ng_words=_SAMPLE_NG_WORDS, client=mock_client, model="x")
        result = checker.check("問題ないメール本文。")
        self.assertTrue(result.discriminatory)
        self.assertFalse(result.is_clean)

    def test_json_parse_error_defaults_to_review(self):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="not valid json")]
        mock_client.messages.create.return_value = mock_resp
        checker = ContentChecker(ng_words=_SAMPLE_NG_WORDS, client=mock_client, model="x")
        result = checker.check("通常メール。")
        self.assertTrue(result.discriminatory)


class TestReloadNGWords(unittest.TestCase):
    def test_reload_updates_word_list(self):
        checker = _make_checker()
        checker.reload_ng_words([{"ワード": "騒音", "カテゴリ": "修繕・設備トラブル"}])
        result = checker.check("騒音がひどいです。")
        self.assertFalse(result.is_clean)
        # Old NG word no longer in list
        result2 = checker.check("クレームです。")
        # discriminatory check still runs (mocked to False), NG scan won't find クレーム
        self.assertEqual([h.word for h in result2.ng_hits], [])


if __name__ == "__main__":
    unittest.main()
