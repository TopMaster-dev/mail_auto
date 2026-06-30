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
from datetime import datetime, timedelta

import bcrypt
from flask import (
    Flask, abort, flash, redirect, render_template, request, url_for,
)
from flask_login import (
    LoginManager, UserMixin, current_user, login_required, login_user, logout_user,
)

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


def create_app(cfg: dict | None = None) -> Flask:
    cfg = cfg or load_config()
    admin_cfg = cfg.get("admin", {})

    app = Flask(__name__)
    app.secret_key = admin_cfg.get("secret_key") or "change-me-in-env"
    app.permanent_session_lifetime = timedelta(
        minutes=int(admin_cfg.get("session_lifetime_minutes", 120)))

    # Behind nginx/TLS: honor X-Forwarded-For so the IP allowlist sees the real client.
    if admin_cfg.get("behind_proxy", False):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    password_hash = (admin_cfg.get("password_hash") or "").encode()
    allowed_networks = _parse_networks(admin_cfg.get("allowed_ips", ""))
    followup_cfg = cfg.get("followup", {})
    followup_enabled = followup_cfg.get("enabled", False)
    steps = followup_cfg.get("steps", [{"days": 2}])
    followup_first_days = int(steps[0]["days"]) if steps else 2

    # Shared backend clients
    sheets = SheetsClient(
        service_account_json=cfg["sheets"]["service_account_json"],
        spreadsheet_id=cfg["sheets"]["spreadsheet_id"],
        sheet_names=cfg["sheets"]["names"],
    )
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
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            pw = request.form.get("password", "").encode()
            if password_hash and bcrypt.checkpw(pw, password_hash):
                login_user(_AdminUser(), remember=True)
                return redirect(url_for("dashboard"))
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
        records = sheets.read_inquiries()
        records.reverse()  # newest first
        if status_filter:
            records = [r for r in records if str(r.get("ステータス", "")).strip() == status_filter]
        counts: dict[str, int] = {}
        for r in sheets.read_inquiries():
            counts[str(r.get("ステータス", "")).strip()] = counts.get(
                str(r.get("ステータス", "")).strip(), 0) + 1
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

    @app.context_processor
    def _inject():
        return {"current_user": current_user}

    return app


if __name__ == "__main__":
    _cfg = load_config()
    _app = create_app(_cfg)
    _app.run(host=_cfg["admin"]["host"], port=int(_cfg["admin"]["port"]), debug=False)
