"""
Flask admin panel for the mail automation system (Phase 2).

The operator reviews AI-generated drafts, edits if needed, sends with one click,
and can stop a customer's follow-up sequence. All data is read from / written to
the same Google Spreadsheet the polling service uses.

Run (dev):     python -m admin.app
Run (prod):    gunicorn admin.wsgi:app -b 0.0.0.0:5000
"""
from __future__ import annotations
import ipaddress
import logging
import threading
import time
from datetime import datetime, timedelta

import bcrypt
from flask import (
    Flask, abort, flash, redirect, render_template, request, url_for,
)
from flask_login import (
    LoginManager, UserMixin, current_user, login_required, login_user, logout_user,
)
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.exceptions import HTTPException

from src.config_loader import load_config
from src.core.models import Inquiry
from src.email_builder.assembler import EmailAssembler
from src.integrations.gmail_client import GmailClient
from src.integrations.sheets_client import SheetsClient

logger = logging.getLogger(__name__)

# Statuses the operator filters by, in display order
STATUS_ORDER = ["自動送信可", "AI返信文生成済み", "要確認", "NG検出", "送信済み", "返信あり"]


class _AdminUser(UserMixin):
    id = "admin"


class LoginThrottle:
    """Lock out an IP after repeated failed logins.

    The panel authenticates with a single password and no second factor, so the
    login form is a brute-force target once nginx exposes it.

    State is in-memory and therefore per-worker — the systemd unit runs gunicorn
    with 2 workers, so the effective allowance is roughly double what is
    configured here. That still cuts the attempt rate by orders of magnitude; if
    the worker count grows, move this to a shared store.
    """

    def __init__(self, max_attempts: int = 5, window_seconds: int = 300,
                 lockout_seconds: int = 900):
        self._max = max_attempts
        self._window = window_seconds
        self._lockout = lockout_seconds
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def retry_after(self, ip: str) -> int:
        """Seconds this IP must wait before trying again (0 = not locked out)."""
        with self._lock:
            remaining = self._locked_until.get(ip, 0.0) - time.monotonic()
            return int(remaining) + 1 if remaining > 0 else 0

    def record_failure(self, ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            recent = [t for t in self._failures.get(ip, []) if now - t < self._window]
            recent.append(now)
            if len(recent) >= self._max:
                self._locked_until[ip] = now + self._lockout
                self._failures.pop(ip, None)
            else:
                self._failures[ip] = recent

    def record_success(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)
            self._locked_until.pop(ip, None)

    def _prune(self, now: float) -> None:
        """Drop expired entries so a distributed attack cannot grow the dicts."""
        for addr, until in list(self._locked_until.items()):
            if until <= now:
                del self._locked_until[addr]
        for addr, stamps in list(self._failures.items()):
            if not any(now - t < self._window for t in stamps):
                del self._failures[addr]


def _parse_networks(allowed: str) -> list:
    nets = []
    for part in (allowed or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            logger.warning("Ignoring invalid ADMIN_ALLOWED_IPS entry: %s", part)
    return nets


def create_app(cfg: dict | None = None, *, sheets=None, gmail=None) -> Flask:
    """Build the admin app.

    `sheets` / `gmail` exist so tests can inject doubles; in production they are
    left unset and the real clients are constructed from config.
    """
    cfg = cfg or load_config()
    admin_cfg = cfg.get("admin", {})

    secret_key = admin_cfg.get("secret_key")
    if not secret_key:
        # The old fallback to a literal default meant an unset env var silently
        # left every session cookie forgeable.
        raise RuntimeError(
            "FLASK_SECRET_KEY が設定されていません。"
            ".env に長いランダム文字列を設定してください（セッション偽造防止のため必須）。")

    lifetime = timedelta(minutes=int(admin_cfg.get("session_lifetime_minutes", 120)))
    secure_cookies = bool(admin_cfg.get("behind_proxy", False))

    app = Flask(__name__)
    app.secret_key = secret_key
    app.permanent_session_lifetime = lifetime
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Served over TLS whenever it sits behind the nginx front end.
        SESSION_COOKIE_SECURE=secure_cookies,
        # login_user(remember=True) issues a cookie that defaults to a year,
        # which quietly overrode session_lifetime_minutes. Tie it to the
        # configured lifetime so that setting means what it says.
        REMEMBER_COOKIE_DURATION=lifetime,
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SAMESITE="Lax",
        REMEMBER_COOKIE_SECURE=secure_cookies,
    )

    # Every state-changing route is a plain POST form. Without a token, any page
    # the logged-in operator visits could make their browser send a customer
    # email or stop a follow-up sequence on their behalf.
    CSRFProtect(app)

    # Behind nginx/TLS: honor X-Forwarded-For so the IP allowlist sees the real client.
    if admin_cfg.get("behind_proxy", False):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    password_hash = (admin_cfg.get("password_hash") or "").encode()
    allowed_networks = _parse_networks(admin_cfg.get("allowed_ips", ""))
    if not allowed_networks:
        logger.warning("ADMIN_ALLOWED_IPS is empty — the admin panel will accept "
                       "connections from any address")
    followup_cfg = cfg.get("followup", {})
    followup_enabled = followup_cfg.get("enabled", False)
    steps = followup_cfg.get("steps", [{"days": 2}])
    followup_first_days = int(steps[0]["days"]) if steps else 2

    # Shared backend clients (injected in tests, built from config in production)
    if sheets is None:
        sheets = SheetsClient(
            service_account_json=cfg["sheets"]["service_account_json"],
            spreadsheet_id=cfg["sheets"]["spreadsheet_id"],
            sheet_names=cfg["sheets"]["names"],
        )
    if gmail is None:
        gmail = GmailClient(
            address=cfg["gmail"]["address"],
            app_password=cfg["gmail"]["app_password"],
            imap_host=cfg["gmail"]["imap_host"],
            smtp_host=cfg["gmail"]["smtp_host"],
            smtp_port=cfg["gmail"]["smtp_port"],
        )

    login_manager = LoginManager(app)
    login_manager.login_view = "login"

    @login_manager.user_loader
    def load_user(user_id):
        return _AdminUser() if user_id == "admin" else None

    # ── IP allowlist ─────────────────────────────────────────────────────────
    @app.before_request
    def _ip_gate():
        if not allowed_networks:
            return
        try:
            ip = ipaddress.ip_address(request.remote_addr or "")
        except ValueError:
            abort(403)
        if not any(ip in net for net in allowed_networks):
            logger.warning("Blocked admin access from %s", request.remote_addr)
            abort(403)

    # ── auth ─────────────────────────────────────────────────────────────────
    def _password_matches(pw: bytes) -> bool:
        if not password_hash:
            logger.error("ADMIN_PASSWORD_HASH is not set — no login can succeed")
            return False
        try:
            return bcrypt.checkpw(pw, password_hash)
        except ValueError:
            # A hash pasted with surrounding quotes or a stray newline raises
            # "Invalid salt", which used to surface as a 500 on the login page.
            logger.error("ADMIN_PASSWORD_HASH is not a valid bcrypt hash — "
                         'regenerate it with: python -m admin.set_password "<password>"')
            return False

    throttle = LoginThrottle(
        max_attempts=int(admin_cfg.get("login_max_attempts", 5)),
        window_seconds=int(admin_cfg.get("login_window_seconds", 300)),
        lockout_seconds=int(admin_cfg.get("login_lockout_seconds", 900)),
    )

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            ip = request.remote_addr or "unknown"

            wait = throttle.retry_after(ip)
            if wait:
                logger.warning("Login attempt from %s rejected — locked out for "
                               "another %ds", ip, wait)
                flash(f"試行回数が多すぎます。約{wait // 60 + 1}分後に再度お試しください。",
                      "danger")
                return render_template("login.html"), 429

            if _password_matches(request.form.get("password", "").encode()):
                throttle.record_success(ip)
                logger.info("Admin logged in from %s", ip)
                login_user(_AdminUser(), remember=True)
                return redirect(url_for("dashboard"))

            throttle.record_failure(ip)
            logger.warning("Failed admin login from %s", ip)
            flash("パスワードが正しくありません。", "danger")
        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("login"))

    # ── dashboard ────────────────────────────────────────────────────────────
    @app.route("/")
    @login_required
    def dashboard():
        status_filter = request.args.get("status", "")
        # One read, not two — the whole sheet used to be fetched twice per page.
        all_records = sheets.read_inquiries()

        counts: dict[str, int] = {}
        for r in all_records:
            status = str(r.get("ステータス", "")).strip()
            counts[status] = counts.get(status, 0) + 1

        records = list(reversed(all_records))  # newest first
        if status_filter:
            records = [r for r in records
                       if str(r.get("ステータス", "")).strip() == status_filter]
        return render_template("dashboard.html", records=records,
                               status_filter=status_filter,
                               status_order=STATUS_ORDER, counts=counts)

    # ── inquiry detail ───────────────────────────────────────────────────────
    @app.route("/inquiry/<iid>")
    @login_required
    def inquiry_detail(iid):
        rec = sheets.get_inquiry(iid)
        if not rec:
            abort(404)
        subject = f"【{rec.get('問い合わせ物件', '')}】お問い合わせありがとうございます"
        return render_template("inquiry.html", rec=rec, subject=subject)

    @app.route("/inquiry/<iid>/save", methods=["POST"])
    @login_required
    def save_inquiry(iid):
        if not sheets.get_inquiry(iid):
            abort(404)
        sheets.set_draft(iid, request.form.get("body", ""))
        sheets.set_memo(iid, request.form.get("memo", ""))
        flash("保存しました。", "success")
        return redirect(url_for("inquiry_detail", iid=iid))

    @app.route("/inquiry/<iid>/send", methods=["POST"])
    @login_required
    def send_inquiry(iid):
        rec = sheets.get_inquiry(iid)
        if not rec:
            abort(404)
        inq = Inquiry.from_sheets_record(rec)
        if not inq.customer_email:
            flash("送信先メールアドレスがありません。", "danger")
            return redirect(url_for("inquiry_detail", iid=iid))

        subject = request.form.get("subject", "").strip() or \
            f"【{inq.inquiry_property_name}】お問い合わせありがとうございます"
        body = request.form.get("body", "")
        html = EmailAssembler.wrap_html(body)
        try:
            sent_mid = gmail.send(inq.customer_email, subject, html, inq.message_id)
        except Exception as e:
            logger.exception("Admin send failed for %s", iid)
            flash(f"送信に失敗しました：{e}", "danger")
            return redirect(url_for("inquiry_detail", iid=iid))

        sheets.set_draft(iid, body)
        sheets.mark_sent(iid, sent_mid)
        sheets.write_send_log(iid, inq.customer_email, subject, sent_mid, "1st")
        if followup_enabled:
            next_at = datetime.now() + timedelta(days=followup_first_days)
            sheets.schedule_followup(iid, next_at, count=1)
        flash("送信しました。追客スケジュールを設定しました。", "success")
        return redirect(url_for("inquiry_detail", iid=iid))

    @app.route("/inquiry/<iid>/stop", methods=["POST"])
    @login_required
    def stop_followup(iid):
        if not sheets.get_inquiry(iid):
            abort(404)
        sheets.advance_followup(iid, 99, None, "追客停止")
        flash("追客を停止しました。", "warning")
        return redirect(url_for("inquiry_detail", iid=iid))

    @app.errorhandler(CSRFError)
    def _handle_csrf(e):
        logger.warning("CSRF validation failed for %s %s from %s",
                       request.method, request.path, request.remote_addr)
        return render_template(
            "error.html",
            message="セッションの有効期限が切れているか、不正なリクエストです。"
                    "ログインし直してから、もう一度お試しください。"), 400

    @app.errorhandler(Exception)
    def _handle_unexpected(e):
        # 403/404 keep their own status; only genuine faults — almost always a
        # Sheets outage — become the friendly page instead of a bare 500.
        if isinstance(e, HTTPException):
            return e
        logger.exception("Unhandled error in the admin panel")
        return render_template("error.html",
                               message=f"{type(e).__name__}: {e}"), 500

    @app.context_processor
    def _inject():
        return {"current_user": current_user}

    return app


if __name__ == "__main__":
    _cfg = load_config()
    _app = create_app(_cfg)
    _app.run(host=_cfg["admin"]["host"], port=int(_cfg["admin"]["port"]), debug=False)
