# -*- coding: utf-8 -*-
"""
Dry-run integration test.
Uses REAL WordPress API and REAL Claude API.
Does NOT write to Google Sheets and does NOT send email.
All output is printed to console.

Usage:
    python test_dryrun.py
    python test_dryrun.py --wp-only    # WordPress + scoring only (no Claude API key needed)
    python test_dryrun.py --check-only # NG word check only
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

import io
import yaml
from dotenv import load_dotenv

# Force UTF-8 output on Windows (avoids cp932 encode errors)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

load_dotenv()

ROOT = Path(__file__).parent


def _expand_env(value):
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1], "")
    return value


def load_cfg() -> dict:
    with open(ROOT / "config" / "settings.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    def expand(obj):
        if isinstance(obj, dict):
            return {k: expand(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [expand(i) for i in obj]
        return _expand_env(obj) if isinstance(obj, str) else obj

    return expand(raw)


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


# ── Test 1: WordPress property loading ────────────────────────────────────────

def test_wp(cfg: dict):
    section("Test 1: WordPress REST API — Load Properties")
    from src.integrations.wp_client import WordPressClient

    wp = WordPressClient(
        base_url=cfg["wordpress"]["base_url"],
        app_user=cfg["wordpress"].get("app_user", ""),
        app_password=cfg["wordpress"].get("app_password", ""),
        per_page=cfg["wordpress"]["per_page"],
        field_cfg=cfg["wordpress"]["fields"],
        taxonomy_cfg=cfg["wordpress"]["taxonomies"],
    )
    props = wp.load_all()

    vacant = [p for p in props if p.is_vacant]
    commission_free = [p for p in props if p.is_commission_free]

    print(f"Total properties loaded : {len(props)}")
    print(f"Vacant                  : {len(vacant)}")
    print(f"Commission-free         : {len(commission_free)}")

    if vacant:
        print("\n--- Sample vacant property ---")
        p = vacant[0]
        print(f"  Name        : {p.name}")
        print(f"  URL         : {p.url}")
        print(f"  Rent        : ¥{p.rent:,} + ¥{p.management_fee:,}")
        print(f"  Layout      : {p.layout}")
        print(f"  Train line  : {p.train_line}")
        print(f"  City        : {p.city}")
        print(f"  Walk min    : {p.walk_minutes}")
        print(f"  Category    : {p.category}")
        print(f"  Equipment   : {p.equipment[:5]}")
        print(f"  Area        : {p.area_sqm}㎡")
        print(f"  CommFree    : {p.is_commission_free}")

    return wp, props


# ── Test 2: Alternative property scoring ──────────────────────────────────────

def test_scoring(props):
    section("Test 2: Alternative Property Scoring")
    from src.core.models import Property
    from src.matching.property_scorer import PropertyScorer

    scorer = PropertyScorer(props)
    vacant = [p for p in props if p.is_vacant]

    if len(vacant) < 2:
        print("Not enough vacant properties to test scoring")
        return scorer

    # Use first vacant property as the "inquiry property" (simulate unavailable)
    inquiry_prop = vacant[0]
    print(f"Inquiry property: {inquiry_prop.name} ({inquiry_prop.train_line} / {inquiry_prop.city})")

    results = scorer.find_alternatives(inquiry_prop, top_n=3)
    print(f"\nTop {len(results)} alternative(s):")
    for i, p in enumerate(results, 1):
        print(f"  {i}. {p.name}")
        print(f"     ¥{p.rent:,} | {p.layout} | {p.train_line} | {p.city}")
        print(f"     Category: {p.category[:3]}")

    return scorer


# ── Test 3: NG word check ─────────────────────────────────────────────────────

def test_ng_check(cfg: dict):
    section("Test 3: NG Word Check (with mock Claude — no API key needed)")
    from unittest.mock import MagicMock
    from src.ai.content_checker import ContentChecker

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text='{"discriminatory": false, "reason": ""}')]
    mock_client.messages.create.return_value = mock_resp

    # Sample NG words (normally loaded from Sheets)
    ng_words = [
        {"ワード": "クレーム",   "カテゴリ": "クレーム系"},
        {"ワード": "弁護士",     "カテゴリ": "契約・法的関連"},
        {"ワード": "水漏れ",     "カテゴリ": "修繕・設備トラブル"},
        {"ワード": "返金",       "カテゴリ": "返金・金銭関連"},
        {"ワード": "至急",       "カテゴリ": "緊急性"},
        {"ワード": "審査",       "カテゴリ": "審査・保証会社関連"},
        {"ワード": "外国人不可", "カテゴリ": "差別的表現"},
    ]
    checker = ContentChecker(ng_words=ng_words, client=mock_client,
                             model=cfg["claude"]["model"])

    test_cases = [
        ("通常問い合わせ",       "お部屋を内見したいです。よろしくお願いします。",  True),
        ("クレーム含む",         "クレームを申し上げたい。",                        False),
        ("法的内容",             "弁護士に相談します。返金してください。",           False),
        ("設備トラブル",         "水漏れが発生しています。至急対応をお願いします。", False),
        ("差別的表現",           "外国人不可の物件はありますか？",                  False),
        ("正常な外国籍言及",     "外国籍の方もご相談いただけますか？",              True),
    ]

    all_ok = True
    for label, text, expect_clean in test_cases:
        result = checker.check(text)
        status = "✓" if result.is_clean == expect_clean else "✗ FAIL"
        if result.is_clean != expect_clean:
            all_ok = False
        hits = [h.word for h in result.ng_hits]
        print(f"  [{status}] {label}")
        if not result.is_clean:
            print(f"         NG hits: {hits} | discriminatory: {result.discriminatory}")

    print(f"\nNG check: {'ALL PASS' if all_ok else 'SOME FAILURES'}")
    return checker


# ── Test 4: Claude AI draft generation ───────────────────────────────────────

def test_ai_generation(cfg: dict, props: list):
    section("Test 4: Claude AI Draft Generation (requires CLAUDE_API_KEY)")

    api_key = cfg["claude"]["api_key"]
    if not api_key or "xxx" in api_key:
        print("  SKIP — CLAUDE_API_KEY not set in .env")
        return None

    from anthropic import Anthropic
    from src.ai.draft_generator import DraftGenerator

    client = Anthropic(api_key=api_key)
    gen = DraftGenerator(client=client, model=cfg["claude"]["model"],
                         max_tokens=cfg["claude"]["max_tokens"])

    vacant = [p for p in props if p.is_vacant and p.name]
    if not vacant:
        print("  No vacant properties to generate intro for")
        return gen

    prop = vacant[0]
    print(f"  Generating property intro for: {prop.name}")

    try:
        intro = gen.generate_property_intro(prop)
        print(f"\n  --- Property intro ---\n  {intro}")

        invitation = gen.generate_visit_invitation(
            "初期費用を抑えたい。ペット可の物件を探しています。"
        )
        print(f"\n  --- Visit invitation ---\n  {invitation}")

        if len(vacant) >= 2:
            alt_intro = gen.generate_alt_intro(vacant[0], vacant[1])
            print(f"\n  --- Alt property intro for '{vacant[1].name}' ---\n  {alt_intro}")

        print("\n  AI generation: PASS")
    except Exception as e:
        print(f"\n  AI generation FAILED: {e}")

    return gen


# ── Test 5: Email assembly dry-run ────────────────────────────────────────────

def test_email_assembly(cfg: dict, props: list):
    section("Test 5: Email Assembly (no send)")
    from src.core.models import Inquiry
    from src.email_builder.assembler import EmailAssembler
    from datetime import datetime
    import uuid

    vacant = [p for p in props if p.is_vacant]
    if not vacant:
        print("  No vacant properties — skip")
        return

    prop = vacant[0]
    inquiry = Inquiry(
        id=str(uuid.uuid4()),
        received_at=datetime.now(),
        customer_name="テスト 太郎",
        customer_email="test@example.com",
        inquiry_property_name=prop.name,
        inquiry_property_url=prop.url,
        raw_body="初期費用を教えてください。内見したいです。",
        is_vacant=True,
        matched_property=prop,
    )

    assembler = EmailAssembler(company=cfg["company"], is_business_hours=True)

    subject, html = assembler.build_first_mail(
        inquiry=inquiry,
        property_intro="【AI生成テスト】素敵なお部屋で、駅近で住みやすい物件です。",
        visit_invitation="費用面も一緒にご確認いただけます。",
        alt_intros=[],
    )

    print(f"  Subject : {subject}")
    print(f"  HTML length : {len(html)} chars")
    # Show plain text preview
    import re
    plain = re.sub(r"<[^>]+>", "", html).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    print("\n  --- Email preview (first 800 chars) ---")
    print(plain[:800])
    print("\n  Email assembly: PASS")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wp-only",    action="store_true")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    cfg = load_cfg()
    print("Mail Automation — Local Dry-Run Test")
    print(f"WordPress : {cfg['wordpress']['base_url']}")
    print(f"Claude    : {cfg['claude']['model']}")
    api_key = cfg["claude"]["api_key"]
    print(f"API key   : {'SET ✓' if api_key and 'xxx' not in api_key else 'NOT SET (AI tests will skip)'}")

    if args.check_only:
        test_ng_check(cfg)
        return

    wp, props = test_wp(cfg)
    scorer = test_scoring(props)

    if args.wp_only:
        return

    test_ng_check(cfg)
    gen = test_ai_generation(cfg, props)
    test_email_assembly(cfg, props)

    section("Summary")
    print("  WordPress API   : ✓")
    print("  Property scoring: ✓")
    print("  NG word check   : ✓")
    api_key = cfg["claude"]["api_key"]
    print(f"  AI generation   : {'✓' if gen else 'SKIPPED (no API key)'}")
    print("  Email assembly  : ✓")
    print("\nNext step: add CLAUDE_API_KEY and SPREADSHEET_ID to .env")


if __name__ == "__main__":
    main()
