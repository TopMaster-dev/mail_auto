from __future__ import annotations
import json
import logging
from pathlib import Path

from anthropic import Anthropic

from src.core.models import Property

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "config" / "prompts"

_SYSTEM = """\
あなたはレントマガジン株式会社の賃貸物件担当者です。
指定された箇所のみJSON形式で出力してください。余計な説明は不要です。

以下は絶対に生成しないでください：
- 会社名・住所・電話番号・メールアドレス・署名
- 割引URL・物件URL・希望日時ひな形
- 空室状況の断定（「空いています」「ご案内可能です」等）
- 審査可否・契約条件・入居可否・申込可否の断定
- 「残り1室」「人気物件」「早いもの順」等の在庫断定
- 初期費用の確定金額
"""


class DraftGenerator:
    """
    Generates three AI-written text segments per inquiry:
    1. property_intro  — for vacant property mail
    2. alt_intro       — for unavailable property mail (per alt property)
    3. visit_invitation — personalised one-liner before the visit CTA
    """

    def __init__(self, client: Anthropic, model: str, max_tokens: int = 600):
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._property_intro_tpl = (_PROMPTS_DIR / "property_intro.txt").read_text(encoding="utf-8")
        self._alt_intro_tpl = (_PROMPTS_DIR / "alt_property_intro.txt").read_text(encoding="utf-8")
        self._visit_tpl = (_PROMPTS_DIR / "visit_invitation.txt").read_text(encoding="utf-8")

    # ── public ───────────────────────────────────────────────────────────────

    def generate_property_intro(self, prop: Property) -> str:
        prompt = self._property_intro_tpl.format(
            property_name=prop.name,
            train_line=prop.train_line,
            nearest_station=prop.nearest_station,
            walk_minutes=prop.walk_minutes,
            layout=prop.layout,
            rent=f"{prop.rent:,}",
            management_fee=f"{prop.management_fee:,}",
            category="、".join(prop.category) if prop.category else "なし",
            equipment="、".join(prop.equipment[:8]) if prop.equipment else "なし",
            city=prop.city,
        )
        result = self._call(prompt, key="intro")
        logger.info("Generated property intro (%d chars)", len(result))
        return result

    def generate_alt_intro(self, original: Property, alt: Property) -> str:
        common = list(set(original.category) & set(alt.category))
        prompt = self._alt_intro_tpl.format(
            original_name=original.name,
            original_station=original.nearest_station,
            original_line=original.train_line,
            original_layout=original.layout,
            original_category="、".join(original.category),
            alt_name=alt.name,
            alt_line=alt.train_line,
            alt_station=alt.nearest_station,
            alt_walk=alt.walk_minutes,
            alt_layout=alt.layout,
            alt_rent=f"{alt.rent:,}",
            alt_category="、".join(alt.category) if alt.category else "なし",
            common_features="、".join(common) if common else "エリアの近さ",
        )
        result = self._call(prompt, key="intro")
        logger.info("Generated alt property intro for '%s' (%d chars)", alt.name, len(result))
        return result

    def generate_visit_invitation(self, inquiry_body: str) -> str:
        prompt = self._visit_tpl.format(inquiry_body=inquiry_body[:400])
        result = self._call(prompt, key="sentence")
        logger.info("Generated visit invitation: %s", result)
        return result

    # ── internal ─────────────────────────────────────────────────────────────

    def _call(self, user_prompt: str, key: str) -> str:
        """
        Call Claude with prompt caching on the system prompt.
        Retries up to 3 times with progressively stricter format instructions.
        On third failure raises RuntimeError → caller sets status to 要確認.
        """
        extra = ""
        for attempt in range(3):
            try:
                resp = self._client.messages.create(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    system=[{
                        "type": "text",
                        "text": _SYSTEM + extra,
                        "cache_control": {"type": "ephemeral"},
                    }],
                    messages=[{"role": "user", "content": user_prompt}],
                )
                raw = resp.content[0].text.strip()
                # Claude sometimes wraps in markdown code blocks
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                data = json.loads(raw)
                return str(data.get(key, "")).strip()
            except json.JSONDecodeError:
                logger.warning("Draft generation attempt %d: JSON parse error. Raw: %s",
                               attempt + 1, resp.content[0].text[:100] if 'resp' in dir() else "")
                extra = "\n必ずJSONのみを出力してください。説明文・マークダウンは禁止。"
            except Exception as e:
                logger.warning("Draft generation attempt %d error: %s", attempt + 1, e)

        raise RuntimeError(f"Draft generation failed after 3 attempts for key='{key}'")
