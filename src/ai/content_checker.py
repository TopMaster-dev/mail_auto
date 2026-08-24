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

# Which mail in the sequence is being screened. Cost words are marked
# 2通目以降 in the spreadsheet: asking the price is a normal first enquiry,
# but the client wants a person involved before chasing someone about money.
STAGE_INITIAL = "initial"
STAGE_FOLLOWUP = "followup"

# 適用段階 column values in the スプレッドシート.
STAGE_ALL = "初回から"
STAGE_FOLLOWUP_ONLY = "2通目以降"

_DISC_SYSTEM = "あなたは不動産業務の法令遵守チェッカーです。指定されたJSON形式のみで回答してください。"

_DISC_PROMPT = """\
以下のテキストを読み、差別的・排除的な表現が含まれているか判定してください。

【判定基準】
「不可」「お断り」「入居できない」「紹介できない」「審査に通らない」と組み合わせて、
外国籍・外国人・生活保護・高齢者・障がい者・ひとり親・LGBT・水商売・夜職・無職・フリーター
などの属性を理由に制限・排除している場合のみ discriminatory: true とする。

属性ワードが出るだけでは true にしない。
「外国籍の方もご相談可能」「高齢の方は一度ご相談ください」は false とする。

あわせて、苦情・不満・感情的な表現が含まれるかを判定してください。

【苦情の判定基準】
怒り・不信・非難・強い不満が表れている場合のみ complaint: true とする。
費用・条件・空室状況などを単に質問しているだけでは false とする。
　例：「初期費用はいくらですか」            → false
　例：「初期費用が高すぎる。納得できません」 → true
　例：「対応が遅くて困っています」           → true
　例：「内見したいのですが可能でしょうか」   → false

【対象テキスト】
{text}

【出力形式】JSONのみ。説明不要。
{{"discriminatory": true/false, "reason": "判定理由（20字以内）",
  "complaint": true/false, "complaint_reason": "判定理由（20字以内）"}}
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
        """Refresh NG word list (called each poll cycle).

        The 適用段階 column lets a word apply only from the follow-up onward.
        Cost words are set that way: asking the price is a routine first
        enquiry, but the client wants a person involved before we chase
        someone about money a second and third time.
        """
        self._ng_words = [
            {"word": unicodedata.normalize("NFKC", w["ワード"]),
             "category": w.get("カテゴリ", "その他"),
             "stage": str(w.get("適用段階", "") or STAGE_ALL).strip()}
            for w in ng_words if w.get("ワード")
        ]
        followup_only = sum(1 for w in self._ng_words if w["stage"] == STAGE_FOLLOWUP_ONLY)
        logger.debug("Loaded %d NG words (%d follow-up only)",
                     len(self._ng_words), followup_only)

    def check(self, text: str, stage: str = STAGE_INITIAL) -> CheckResult:
        """
        Run both checks. Both always run, even when the NG scan already hit:
        the outcome is 要確認 either way, but the 要確認履歴 entry should record
        every reason the mail was held, not just the first one found.

        `stage` selects which words apply — see reload_ng_words.
        """
        normalized = unicodedata.normalize("NFKC", text)
        ng_hits = self._scan_ng(normalized, stage)
        disc, disc_reason, complaint, complaint_reason = self._check_context(text)

        return CheckResult(
            is_clean=not ng_hits and not disc and not complaint,
            ng_hits=ng_hits,
            discriminatory=disc,
            discriminatory_reason=disc_reason,
            complaint=complaint,
            complaint_reason=complaint_reason,
        )

    def check_generated(self, segments: list[str]) -> CheckResult:
        """Screen only the AI-written parts of a reply.

        The assembled mail also contains the fixed template the client supplied,
        and that template itself contains 初期費用 and 仲介手数料 — words their own
        NG list includes. Scanning the whole body would therefore flag every
        draft permanently and nothing could ever be sent. Spec 17-2 asks for the
        AI-generated reply text to be screened, which is exactly what this does.
        """
        text = "\n\n".join(s for s in segments if s and s.strip())
        if not text:
            # Nothing was generated for this mail, so there is nothing to screen.
            return CheckResult(is_clean=True, ng_hits=[], discriminatory=False,
                               discriminatory_reason="")
        # Always the initial stage, whichever mail this is. The staged words
        # describe what a *customer* is asking about; our own copy legitimately
        # mentions 仲介手数料 when a listing is commission-free, and roughly half
        # of generated intros do. Applying them here would block follow-ups the
        # client has asked to keep sending automatically.
        return self.check(text, stage=STAGE_INITIAL)

    # ── NG word scan ─────────────────────────────────────────────────────────

    def _scan_ng(self, normalized_text: str, stage: str = STAGE_INITIAL) -> list[NGHit]:
        hits: list[NGHit] = []
        seen: set[str] = set()
        for entry in self._ng_words:
            if entry["stage"] == STAGE_FOLLOWUP_ONLY and stage == STAGE_INITIAL:
                continue
            word_norm = entry["word"]
            if word_norm in normalized_text and word_norm not in seen:
                hits.append(NGHit(word=entry["word"], category=entry["category"]))
                seen.add(word_norm)
        if hits:
            logger.info("NG words found: %s", [h.word for h in hits])
        return hits

    # ── discriminatory expression check ──────────────────────────────────────

    def _check_context(self, text: str) -> tuple[bool, str, bool, str]:
        """
        Judge both context questions in one call.

        Returns (discriminatory, reason, complaint, complaint_reason).
        On API error defaults to discriminatory=True — safe-fail to 要確認.

        Both judgements ride on a single request: they read the same text, and
        splitting them would double the per-mail API cost for no benefit.
        """
        prompt = _DISC_PROMPT.format(text=text[:2000])
        for attempt in range(3):
            try:
                resp = self._client.messages.create(
                    model=self._model,
                    max_tokens=250,
                    system=_DISC_SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = resp.content[0].text.strip()
                result = _parse_json_lenient(raw)
                disc = bool(result.get("discriminatory", False))
                reason = str(result.get("reason", ""))
                complaint = bool(result.get("complaint", False))
                complaint_reason = str(result.get("complaint_reason", ""))
                if disc:
                    logger.info("Discriminatory expression detected: %s", reason)
                if complaint:
                    logger.info("Complaint / dissatisfaction detected: %s", complaint_reason)
                return disc, reason, complaint, complaint_reason
            except json.JSONDecodeError:
                logger.warning("Context check attempt %d: JSON parse error", attempt + 1)
            except Exception as e:
                logger.warning("Context check attempt %d error: %s", attempt + 1, e)
        logger.error("Context check failed after 3 attempts — defaulting to requires_review")
        return True, "判定エラー（要確認）", False, ""
