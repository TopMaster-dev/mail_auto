"""
Admin panel: authentication, session config and CSRF.

Every state-changing route is a plain POST form. Without a CSRF token, any page
the logged-in operator happened to visit could make their browser send a mail to
a customer or stop a follow-up sequence.
"""
import re
import unittest
from unittest.mock import MagicMock

import bcrypt

from admin.app import create_app

_PASSWORD = "test-password"
_TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _cfg(password_hash: bytes | None = None) -> dict:
    if password_hash is None:
        password_hash = bcrypt.hashpw(_PASSWORD.encode(), bcrypt.gensalt())
    return {
        "admin": {
            "secret_key": "unit-test-secret-key",
            "password_hash": password_hash.decode(),
            "session_lifetime_minutes": 120,
            "behind_proxy": False,
            "allowed_ips": "",
        },
        "followup": {"enabled": True, "steps": [{"days": 2}, {"days": 3}]},
        "company": {"staff_name": "担当者"},
    }


def _record() -> dict:
    return {
        "ID": "abc123", "顧客名": "太郎", "メールアドレス": "taro@example.com",
        "問い合わせ物件": "テスト物件", "物件URL": "", "空室有無": "あり",
        "ステータス": "自動送信可", "AI返信文案": "文案本文",
        "追客ステータス": "追客中", "追客回数": "0", "担当者メモ": "",
    }


class _AdminTestCase(unittest.TestCase):
    def setUp(self):
        self.sheets = MagicMock()
        self.sheets.read_inquiries.return_value = [_record()]
        self.sheets.get_inquiry.return_value = _record()
        self.gmail = MagicMock()
        self.gmail.send.return_value = "<mid@rentmagazine.jp>"
        self.app = create_app(_cfg(), sheets=self.sheets, gmail=self.gmail)
        self.client = self.app.test_client()

    def _token(self, path: str = "/login") -> str:
        html = self.client.get(path).get_data(as_text=True)
        match = _TOKEN_RE.search(html)
        self.assertIsNotNone(match, f"no csrf_token field rendered on {path}")
        return match.group(1)

    def _login(self):
        return self.client.post(
            "/login", data={"password": _PASSWORD, "csrf_token": self._token()})


class TestAuthentication(_AdminTestCase):
    def test_dashboard_requires_login(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_correct_password_logs_in(self):
        self.assertEqual(self._login().status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_wrong_password_does_not_log_in(self):
        self.client.post(
            "/login", data={"password": "wrong", "csrf_token": self._token()})
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_logout_ends_the_session(self):
        self._login()
        self.client.get("/logout")
        self.assertEqual(self.client.get("/").status_code, 302)


class TestCsrfProtection(_AdminTestCase):
    def test_login_form_renders_a_token(self):
        self.assertTrue(self._token("/login"))

    def test_login_without_token_is_rejected(self):
        resp = self.client.post("/login", data={"password": _PASSWORD})
        self.assertEqual(resp.status_code, 400)

    def test_send_without_token_never_mails_the_customer(self):
        self._login()
        resp = self.client.post(
            "/inquiry/abc123/send", data={"subject": "件名", "body": "本文"})
        self.assertEqual(resp.status_code, 400)
        self.gmail.send.assert_not_called()

    def test_stop_followup_without_token_is_rejected(self):
        self._login()
        self.assertEqual(self.client.post("/inquiry/abc123/stop").status_code, 400)
        self.sheets.advance_followup.assert_not_called()

    def test_save_without_token_is_rejected(self):
        self._login()
        resp = self.client.post(
            "/inquiry/abc123/save", data={"body": "本文", "memo": "メモ"})
        self.assertEqual(resp.status_code, 400)
        self.sheets.set_draft.assert_not_called()

    def test_send_with_token_succeeds(self):
        self._login()
        resp = self.client.post(
            "/inquiry/abc123/send",
            data={"subject": "件名", "body": "本文",
                  "csrf_token": self._token("/inquiry/abc123")})
        self.assertEqual(resp.status_code, 302)
        self.gmail.send.assert_called_once()

    def test_stop_followup_with_token_succeeds(self):
        self._login()
        resp = self.client.post(
            "/inquiry/abc123/stop",
            data={"csrf_token": self._token("/inquiry/abc123")})
        self.assertEqual(resp.status_code, 302)
        self.sheets.advance_followup.assert_called_once()


class TestSessionConfiguration(_AdminTestCase):
    def test_remember_cookie_matches_the_configured_lifetime(self):
        # login_user(remember=True) otherwise issues a cookie lasting a year,
        # silently overriding session_lifetime_minutes.
        self.assertEqual(self.app.config["REMEMBER_COOKIE_DURATION"],
                         self.app.permanent_session_lifetime)

    def test_cookies_are_httponly_and_samesite(self):
        for key in ("SESSION_COOKIE_HTTPONLY", "REMEMBER_COOKIE_HTTPONLY"):
            self.assertTrue(self.app.config[key], key)
        for key in ("SESSION_COOKIE_SAMESITE", "REMEMBER_COOKIE_SAMESITE"):
            self.assertEqual(self.app.config[key], "Lax", key)

    def test_secure_cookies_follow_the_proxy_setting(self):
        cfg = _cfg()
        cfg["admin"]["behind_proxy"] = True
        app = create_app(cfg, sheets=MagicMock(), gmail=MagicMock())
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])
        self.assertTrue(app.config["REMEMBER_COOKIE_SECURE"])


class TestStartupValidation(unittest.TestCase):
    def test_missing_secret_key_refuses_to_start(self):
        cfg = _cfg()
        cfg["admin"]["secret_key"] = ""
        with self.assertRaises(RuntimeError):
            create_app(cfg, sheets=MagicMock(), gmail=MagicMock())

    def test_malformed_password_hash_fails_login_without_a_500(self):
        app = create_app(_cfg(password_hash=b"not-a-bcrypt-hash"),
                         sheets=MagicMock(), gmail=MagicMock())
        client = app.test_client()
        token = _TOKEN_RE.search(
            client.get("/login").get_data(as_text=True)).group(1)
        resp = client.post(
            "/login", data={"password": _PASSWORD, "csrf_token": token})
        self.assertEqual(resp.status_code, 200)   # re-rendered login, not a crash


if __name__ == "__main__":
    unittest.main()
