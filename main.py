"""
mail_automation — Phase 1 entry point.

Usage:
    python main.py              # run polling loop
    python main.py --once       # run one cycle and exit (for testing)
    python main.py --probe-wp   # probe WordPress REST API and print field names
"""
from __future__ import annotations
import argparse
import logging
import logging.handlers
import os
import signal
import sys
import time
from pathlib import Path

import yaml
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

# ── logging setup ─────────────────────────────────────────────────────────────
_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            _LOG_DIR / "mail_automation.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        ),
    ],
)

logger = logging.getLogger(__name__)


def _expand_env(value: str) -> str:
    """Replace ${VAR} in config strings with env var values."""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        key = value[2:-1]
        v = os.environ.get(key, "")
        if not v:
            logger.warning("Env var %s is not set", key)
        return v
    return value


def load_config() -> dict:
    cfg_path = Path(__file__).parent / "config" / "settings.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    def expand(obj):
        if isinstance(obj, dict):
            return {k: expand(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [expand(i) for i in obj]
        if isinstance(obj, str):
            return _expand_env(obj)
        return obj

    return expand(raw)


def _validate_config(cfg: dict) -> None:
    required = [
        ("gmail.address", cfg["gmail"]["address"]),
        ("gmail.app_password", cfg["gmail"]["app_password"]),
        ("claude.api_key", cfg["claude"]["api_key"]),
        ("sheets.service_account_json", cfg["sheets"]["service_account_json"]),
        ("sheets.spreadsheet_id", cfg["sheets"]["spreadsheet_id"]),
        ("wordpress.base_url", cfg["wordpress"]["base_url"]),
    ]
    missing = [name for name, val in required if not val or "your_" in val or "xxxxxxx" in val]
    if missing:
        logger.error("Missing required config values: %s", missing)
        sys.exit(1)


def build_processor(cfg: dict):
    """Instantiate and wire all components."""
    from src.ai.content_checker import ContentChecker
    from src.ai.draft_generator import DraftGenerator
    from src.core.inquiry_processor import InquiryProcessor
    from src.email_builder.send_gate import SendGate
    from src.integrations.gmail_client import GmailClient
    from src.integrations.sheets_client import SheetsClient
    from src.integrations.wp_client import WordPressClient
    from src.matching.property_scorer import PropertyScorer

    gmail = GmailClient(
        address=cfg["gmail"]["address"],
        app_password=cfg["gmail"]["app_password"],
        imap_host=cfg["gmail"]["imap_host"],
        smtp_host=cfg["gmail"]["smtp_host"],
        smtp_port=cfg["gmail"]["smtp_port"],
    )

    sheets = SheetsClient(
        service_account_json=cfg["sheets"]["service_account_json"],
        spreadsheet_id=cfg["sheets"]["spreadsheet_id"],
        sheet_names=cfg["sheets"]["names"],
    )

    wp = WordPressClient(
        base_url=cfg["wordpress"]["base_url"],
        app_user=cfg["wordpress"]["app_user"],
        app_password=cfg["wordpress"]["app_password"],
        per_page=cfg["wordpress"]["per_page"],
        field_cfg=cfg["wordpress"]["fields"],
        taxonomy_cfg=cfg["wordpress"]["taxonomies"],
    )

    claude = Anthropic(api_key=cfg["claude"]["api_key"])

    checker = ContentChecker(
        ng_words=sheets.read_ng_words(),
        client=claude,
        model=cfg["claude"]["model"],
    )

    generator = DraftGenerator(
        client=claude,
        model=cfg["claude"]["model"],
        max_tokens=cfg["claude"]["max_tokens"],
    )

    # Load all WP properties on startup
    properties = wp.load_all()
    scorer = PropertyScorer(properties)

    gate = SendGate(
        global_require_confirmation=cfg["send"]["all_require_confirmation"],
    )

    return InquiryProcessor(
        gmail=gmail,
        sheets=sheets,
        wp=wp,
        checker=checker,
        generator=generator,
        scorer=scorer,
        gate=gate,
        company=cfg["company"],
    ), cfg["gmail"]["poll_interval_seconds"]


def probe_wordpress(cfg: dict) -> None:
    """Print WordPress API endpoint info to help identify field names."""
    import requests
    from requests.auth import HTTPBasicAuth
    base = cfg["wordpress"]["base_url"]
    user = cfg["wordpress"]["app_user"]
    pw = cfg["wordpress"]["app_password"]
    auth = HTTPBasicAuth(user, pw) if user else None

    print("\n── WordPress REST API Probe ────────────────────────")
    # 1. Types
    r = requests.get(f"{base}/wp-json/wp/v2/types", auth=auth, timeout=15)
    types_data = r.json()
    print("\nRegistered post types:")
    for slug, info in types_data.items():
        print(f"  {slug}: {info.get('name', '')} (rest_base={info.get('rest_base', '')})")

    # 2. Single estate post — show all keys
    r2 = requests.get(f"{base}/wp-json/wp/v2/estate",
                      params={"per_page": 1, "status": "publish"}, auth=auth, timeout=15)
    posts = r2.json()
    if posts:
        p = posts[0]
        print(f"\nSample estate post fields (ID={p.get('id')}):")
        for k, v in p.items():
            if k not in ("content", "excerpt", "guid", "yoast_head", "yoast_head_json"):
                print(f"  {k}: {str(v)[:120]}")
        print("\nACF / meta fields:")
        for k, v in (p.get("acf") or {}).items():
            print(f"  acf.{k}: {str(v)[:80]}")
        for k, v in (p.get("meta") or {}).items():
            print(f"  meta.{k}: {str(v)[:80]}")
    else:
        print("No estate posts returned (check auth or post_type slug)")
    print("────────────────────────────────────────────────────\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true",
                        help="Run one poll cycle and exit")
    parser.add_argument("--probe-wp", action="store_true",
                        help="Print WordPress field names and exit")
    args = parser.parse_args()

    cfg = load_config()
    _validate_config(cfg)

    if args.probe_wp:
        probe_wordpress(cfg)
        return

    processor, interval = build_processor(cfg)

    if args.once:
        logger.info("Running single poll cycle (--once mode)")
        processor.run_cycle()
        return

    # ── continuous polling loop ───────────────────────────────────────────────
    running = True

    def _shutdown(sig, frame):
        nonlocal running
        logger.info("Shutdown signal received — stopping after current cycle")
        running = False

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info("Mail automation started. Poll interval: %ds", interval)
    while running:
        try:
            processor.run_cycle()
        except Exception as e:
            logger.exception("Unexpected error in poll cycle: %s", e)
        if running:
            time.sleep(interval)

    logger.info("Mail automation stopped")


if __name__ == "__main__":
    main()
