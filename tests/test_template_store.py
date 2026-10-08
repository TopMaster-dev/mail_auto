"""
Operator-editable templates (client item ③).

Three properties matter more than the substitution itself:

* an unset template means "use the built-in text", not "broken" — otherwise
  shipping the feature against an empty sheet would stop every mail;
* a set-but-broken template stops sending rather than quietly reverting to the
  previous wording, which the client would never notice (2026-10-07);
* an unreadable sheet is an error, not an absence — "nothing configured" would
  silently revert them too.
"""
import unittest
from unittest.mock import MagicMock

from src.email_builder import template_store as ts
from src.email_builder.template_store import TemplateStore, render, validate

GOOD_BODY = """\
{顧客名}様

レントマガジン株式会社の{担当者名}と申します。

◆お問い合わせ物件
物件名：{物件名}
{物件URL}

{AI紹介文}

{署名}"""


def _rows(**kw):
    """kw like first_body=..., second_subject=... → sheet rows."""
    name = {"first": ts.FIRST, "second": ts.SECOND, "third": ts.THIRD}
    out = []
    for k, v in kw.items():
        kind, part = k.rsplit("_", 1)
        row = (ts.SUBJECT_ROW if part == "subject" else ts.BODY_ROW)[name[kind]]
        out.append({"テンプレート名": row, "内容": v})
    return out


def _store(rows=None, checker=None, fail=False):
    sheets = MagicMock()
    if fail:
        sheets.read_templates.side_effect = RuntimeError("sheet gone")
    else:
        sheets.read_templates.return_value = rows or []
    s = TemplateStore(sheets, checker=checker)
    s.refresh()
    return s


class TestUnsetIsNotBroken(unittest.TestCase):
    def test_an_empty_sheet_blocks_nothing(self):
        s = _store([])
        for kind in ts.KINDS:
            self.assertEqual(s.blocking_errors(kind), [])
            self.assertIsNone(s.usable(kind))

    def test_blank_rows_count_as_unset(self):
        s = _store(_rows(first_subject="  ", first_body="\n"))
        self.assertEqual(s.blocking_errors(ts.FIRST), [])
        self.assertIsNone(s.usable(ts.FIRST))

    def test_one_configured_mail_does_not_affect_the_others(self):
        s = _store(_rows(second_subject="件名", second_body=GOOD_BODY))
        self.assertIsNotNone(s.usable(ts.SECOND))
        self.assertIsNone(s.usable(ts.FIRST))
        self.assertEqual(s.blocking_errors(ts.FIRST), [])


class TestBrokenStopsRatherThanFallingBack(unittest.TestCase):
    def test_an_unknown_placeholder_blocks(self):
        s = _store(_rows(first_subject="【{物件名称}】", first_body=GOOD_BODY))
        errs = s.blocking_errors(ts.FIRST)
        self.assertTrue(errs)
        self.assertIn("{物件名称}", errs[0])

    def test_a_broken_template_is_never_offered_as_usable(self):
        """usable() returning None must not be read as 'fall back and send'."""
        s = _store(_rows(first_subject="【{物件名称}】", first_body=GOOD_BODY))
        self.assertIsNone(s.usable(ts.FIRST))
        self.assertTrue(s.blocking_errors(ts.FIRST))

    def test_unbalanced_braces_block(self):
        s = _store(_rows(first_subject="件名", first_body="{顧客名様\n{署名}"))
        self.assertTrue(any("括弧" in e for e in s.blocking_errors(ts.FIRST)))

    def test_an_empty_placeholder_blocks(self):
        s = _store(_rows(first_subject="件名", first_body="{}様\n{署名}"))
        self.assertTrue(s.blocking_errors(ts.FIRST))

    def test_a_subject_set_without_a_body_blocks(self):
        s = _store(_rows(first_subject="件名"))
        self.assertTrue(any("本文が空" in e for e in s.blocking_errors(ts.FIRST)))

    def test_a_valid_template_blocks_nothing(self):
        s = _store(_rows(first_subject="【{物件名}】ありがとうございます",
                         first_body=GOOD_BODY))
        self.assertEqual(s.blocking_errors(ts.FIRST), [])
        self.assertIsNotNone(s.usable(ts.FIRST))


class TestContentScreening(unittest.TestCase):
    def _checker(self, clean, words=(), disc=False):
        c = MagicMock()
        r = MagicMock()
        r.is_clean = clean
        r.ng_hits = [MagicMock(word=w) for w in words]
        r.discriminatory = disc
        r.discriminatory_reason = "外国籍を理由に排除"
        c.check.return_value = r
        return c

    def test_an_ng_word_in_the_operators_text_blocks(self):
        s = _store(_rows(first_subject="件名", first_body=GOOD_BODY),
                   checker=self._checker(False, words=["ぼったくり"]))
        self.assertTrue(any("ぼったくり" in e for e in s.blocking_errors(ts.FIRST)))

    def test_discriminatory_wording_blocks(self):
        s = _store(_rows(first_subject="件名", first_body=GOOD_BODY),
                   checker=self._checker(False, disc=True))
        self.assertTrue(any("差別" in e for e in s.blocking_errors(ts.FIRST)))

    def test_clean_text_passes(self):
        s = _store(_rows(first_subject="件名", first_body=GOOD_BODY),
                   checker=self._checker(True))
        self.assertEqual(s.blocking_errors(ts.FIRST), [])

    def test_an_unset_template_is_not_screened(self):
        c = self._checker(True)
        _store([], checker=c)
        c.check.assert_not_called()

    def test_a_checker_failure_does_not_block(self):
        c = MagicMock()
        c.check.side_effect = RuntimeError("api down")
        s = _store(_rows(first_subject="件名", first_body=GOOD_BODY), checker=c)
        self.assertEqual(s.blocking_errors(ts.FIRST), [])


class TestUnreadableSheet(unittest.TestCase):
    def test_a_read_failure_blocks_every_mail(self):
        """Not 'nothing configured' — that would silently revert the wording."""
        s = _store(fail=True)
        for kind in ts.KINDS:
            self.assertTrue(s.blocking_errors(kind))

    def test_the_failure_is_reported_for_the_banner(self):
        self.assertTrue(_store(fail=True).all_errors())


class TestRendering(unittest.TestCase):
    CTX = {"顧客名": "大寺 啓未", "物件名": "ボニートロッサI107",
           "物件URL": "https://x/", "AI紹介文": "紹介文です",
           "最寄駅": "名鉄本線「牛田」", "担当者名": "新家", "署名": "— 署名 —"}

    def test_placeholders_are_substituted(self):
        out = render(GOOD_BODY, self.CTX)
        self.assertIn("大寺 啓未様", out)
        self.assertIn("物件名：ボニートロッサI107", out)
        self.assertIn("新家と申します", out)
        self.assertNotIn("{", out)

    def test_a_known_placeholder_with_no_value_becomes_empty(self):
        out = render("[{最寄駅}]", {})
        self.assertEqual(out, "[]")

    def test_surrounding_text_is_preserved_exactly(self):
        out = render("前{顧客名}後", self.CTX)
        self.assertEqual(out, "前大寺 啓未後")

    def test_the_same_placeholder_may_repeat(self):
        self.assertEqual(render("{物件名}/{物件名}", self.CTX),
                         "ボニートロッサI107/ボニートロッサI107")

    def test_an_unknown_token_is_left_intact_not_dropped(self):
        """Validation already refused this; losing the text would be worse."""
        self.assertEqual(render("a{未知}b", self.CTX), "a{未知}b")

    def test_the_operators_typography_is_sent_unchanged(self):
        """NFKC would rewrite 「物件名：」 as 「物件名:」 in the customer's mail."""
        out = render("物件名：{物件名}　（全角スペース）１２３", self.CTX)
        self.assertIn("物件名：", out)
        self.assertIn("　（全角スペース）", out)
        self.assertIn("１２３", out)


class TestFullWidthBraces(unittest.TestCase):
    """｛｝ is easy to type by accident and would reach the customer verbatim."""

    def test_full_width_braces_are_refused(self):
        errs = validate("件名", "｛顧客名｝様")
        self.assertTrue(any("全角" in e for e in errs))

    def test_half_width_braces_are_accepted(self):
        self.assertEqual(validate("件名", "{顧客名}様"), [])

    def test_a_full_width_brace_is_not_substituted_so_it_must_be_caught(self):
        self.assertEqual(render("｛顧客名｝", TestRendering.CTX), "｛顧客名｝")


class TestPlaceholderHelp(unittest.TestCase):
    def test_every_placeholder_is_documented_for_the_sheet(self):
        rows = ts.placeholder_help()
        self.assertEqual(len(rows), len(ts.PLACEHOLDERS))
        for token, desc in rows:
            self.assertTrue(token.startswith("{") and token.endswith("}"))
            self.assertTrue(desc.strip())

    def test_the_documented_tokens_all_validate(self):
        body = "".join(t for t, _ in ts.placeholder_help())
        self.assertEqual(validate("件名", body), [])


if __name__ == "__main__":
    unittest.main()
