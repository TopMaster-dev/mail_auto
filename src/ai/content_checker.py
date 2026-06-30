from __future__ import annotations
import json
import logging
import re
import unicodedata

from anthropic import Anthropic

from src.core.models import CheckResult, NGHit

logger = logging.getLogger(__name__)

_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json_lenient(raw: str) -> dict:
    """Parse JSON from a model reply that may wrap it in prose or a code fence."""
    cleaned = _CODE_FENCE.sub("", raw.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = _JSON_OBJECT.search(cleaned)
        if m:
            return json.loads(m.group(0))
        raise

_DISC_SYSTEM = "あなたは不動産業務の法令遵守チェッカーです。指定されたJSON形式のみで回答してください。"

_DISC_PROMPT = """\
以下のテキストを読み、差別的・排除的な表現が含まれているか判定してください。

【判定基準】
「不可」「お断り」「入居できない」「紹介できない」「審査に通らない」と組み合わせて、
外国籍・外国人・生活保護・高齢者・障がい者・ひとり親・LGBT・水商売・夜職・無職・フリーター
などの属性を理由に制限・排除している場合のみ discriminatory: true とする。

属性ワードが出るだけでは true にしない。
「外国籍の方もご相談可能」「高齢の方は一度ご相談ください」は false とする。

【対象テキスト】
{text}

【出力形式】JSONのみ。説明不要。
{{"discriminatory": true/false, "reason": "判定理由（20字以内）"}}
"""


class ContentChecker:
    """
    Two-stage content safety check:
    1. NG word scan (rule-based, configurable list from Sheets)
    2. Discriminatory expression check (Claude API context judgment)
    """

    def __init__(self, ng_words: list[dict], client: Anthropic, model: str):
        self._model = model
        self._client = client
        self.reload_ng_words(ng_words)

    def reload_ng_words(self, ng_words: list[dict]) -> None:
        """Refresh NG word list (called each poll cycle)."""
        self._ng_words = [
            {"word": unicodedata.normalize("NFKC", w["ワード"]),
             "category": w.get("カテゴリ", "その他")}
            for w in ng_words if w.get("ワード")
        ]
        logger.debug("Loaded %d NG words", len(self._ng_words))

    def check(self, text: str) -> CheckResult:
        """
        Run both checks. Discriminatory check is skipped if NG words
        already found (status is 要確認 regardless).
        """
        normalized = unicodedata.normalize("NFKC", text)
        ng_hits = self._scan_ng(normalized)
        disc, disc_reason = self._check_discriminatory(text)

        is_clean = not ng_hits and not disc
        return CheckResult(
            is_clean=is_clean,
            ng_hits=ng_hits,
            discriminatory=disc,
            discriminatory_reason=disc_reason,
        )

    # ── NG word scan ─────────────────────────────────────────────────────────

    def _scan_ng(self, normalized_text: str) -> list[NGHit]:
        hits: list[NGHit] = []
        seen: set[str] = set()
        for entry in self._ng_words:
            word_norm = entry["word"]
            if word_norm in normalized_text and word_norm not in seen:
                hits.append(NGHit(word=entry["word"], category=entry["category"]))
                seen.add(word_norm)
        if hits:
            logger.info("NG words found: %s", [h.word for h in hits])
        return hits

    # ── discriminatory expression check ──────────────────────────────────────

    def _check_discriminatory(self, text: str) -> tuple[bool, str]:
        """
        Returns (is_discriminatory, reason).
        On API error defaults to (True, "判定エラー") — safe-fail to 要確認.
        """
        prompt = _DISC_PROMPT.format(text=text[:2000])
        for attempt in range(3):
            try:
                resp = self._client.messages.create(
                    model=self._model,
                    max_tokens=150,
                    system=_DISC_SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = resp.content[0].text.strip()
                result = _parse_json_lenient(raw)
                disc = bool(result.get("discriminatory", False))
                reason = str(result.get("reason", ""))
                if disc:
                    logger.info("Discriminatory expression detected: %s", reason)
                return disc, reason
            except json.JSONDecodeError:
                logger.warning("Discriminatory check attempt %d: JSON parse error", attempt + 1)
            except Exception as e:
                logger.warning("Discriminatory check attempt %d error: %s", attempt + 1, e)
        logger.error("Discriminatory check failed after 3 attempts — defaulting to requires_review")
        return True, "判定エラー（要確認）"
