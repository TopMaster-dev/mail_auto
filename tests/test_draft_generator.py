"""Unit tests for DraftGenerator — mocks Claude API."""
import json
import unittest
from unittest.mock import MagicMock, patch

from src.ai.draft_generator import DraftGenerator
from src.core.models import Property


def _make_generator(response_text: str) -> DraftGenerator:
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text=response_text)]
    mock_client.messages.create.return_value = mock_resp
    return DraftGenerator(client=mock_client, model="test-model")


def _prop() -> Property:
    return Property(
        wp_id=1, name="テスト物件", url="https://rentmagazine.jp/1",
        rent=80000, management_fee=5000, layout="1LDK",
        nearest_station="知立駅", train_line="名鉄三河線", city="知立市",
        walk_minutes=5, category=["デザイナーズ"],
        equipment=["追い焚き", "宅配BOX", "オートロック"],
        is_vacant=True, is_commission_free=False,
        area_sqm=42.0, building_type="マンション",
    )


class TestPropertyIntro(unittest.TestCase):
    def test_returns_intro_text(self):
        gen = _make_generator('{"intro": "素敵な物件です。"}')
        result = gen.generate_property_intro(_prop())
        self.assertEqual(result, "素敵な物件です。")

    def test_strips_markdown_code_block(self):
        gen = _make_generator('```json\n{"intro": "マークダウン内の文章。"}\n```')
        result = gen.generate_property_intro(_prop())
        self.assertEqual(result, "マークダウン内の文章。")

    def test_raises_after_three_json_failures(self):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="not json at all")]
        mock_client.messages.create.return_value = mock_resp
        gen = DraftGenerator(client=mock_client, model="x")
        with self.assertRaises(RuntimeError):
            gen.generate_property_intro(_prop())


class TestRequestShape(unittest.TestCase):
    """The shared prefix must stay byte-identical across retries."""

    def _client_returning(self, *texts):
        client = MagicMock()
        client.messages.create.side_effect = [
            MagicMock(content=[MagicMock(text=t)]) for t in texts]
        return client

    def test_system_is_a_plain_string(self):
        # A list-of-blocks with cache_control cached nothing at this prompt size;
        # see the note in DraftGenerator._call.
        client = self._client_returning('{"intro": "ok"}')
        DraftGenerator(client=client, model="x").generate_property_intro(_prop())
        self.assertIsInstance(client.messages.create.call_args.kwargs["system"], str)

    def test_retry_instruction_goes_to_the_user_turn(self):
        client = self._client_returning("not json", '{"intro": "ok"}')
        result = DraftGenerator(client=client, model="x").generate_property_intro(_prop())
        self.assertEqual(result, "ok")

        first, second = client.messages.create.call_args_list
        # System prompt unchanged between attempts...
        self.assertEqual(first.kwargs["system"], second.kwargs["system"])
        # ...and the stricter instruction landed in the user message instead.
        first_user = first.kwargs["messages"][0]["content"]
        second_user = second.kwargs["messages"][0]["content"]
        self.assertNotEqual(first_user, second_user)
        self.assertIn("必ずJSONのみ", second_user)

    def test_empty_response_content_is_retried_not_crashed(self):
        client = MagicMock()
        client.messages.create.side_effect = [
            MagicMock(content=[]),                          # refusal / truncation
            MagicMock(content=[MagicMock(text='{"intro": "recovered"}')]),
        ]
        result = DraftGenerator(client=client, model="x").generate_property_intro(_prop())
        self.assertEqual(result, "recovered")


class TestAltPropertyIntro(unittest.TestCase):
    def test_returns_intro_text(self):
        gen = _make_generator('{"intro": "こちらもいかがでしょうか。"}')
        result = gen.generate_alt_intro(_prop(), _prop())
        self.assertEqual(result, "こちらもいかがでしょうか。")


class TestVisitInvitation(unittest.TestCase):
    def test_returns_sentence(self):
        gen = _make_generator('{"sentence": "費用面も一緒にご確認いただけます。"}')
        result = gen.generate_visit_invitation("初期費用を抑えたいです。")
        self.assertEqual(result, "費用面も一緒にご確認いただけます。")

    def test_empty_sentence_fallback_is_empty_string(self):
        gen = _make_generator('{"sentence": ""}')
        result = gen.generate_visit_invitation("普通の問い合わせ。")
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
